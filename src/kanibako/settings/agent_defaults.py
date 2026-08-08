"""Per-agent plugin defaults — thin reader of each plugin's shipped defaults file.

Each agent plugin package (``kanibako.plugins.claude`` / ``…goose`` / ``…codex``)
owns ONE declarative defaults file (``<agent>-defaults.yaml``) holding that
agent's complete DEFAULT-SET — its :class:`~kanibako.targets.base.PluginDescriptor`
data (bindings, launch grammar, settings, container env, cred files, …) plus its
AGENT-scope ``default_common`` — in the specced structured form.  This mirrors how
the system/core defaults ship (:mod:`kanibako.settings.core_defaults` /
``kanibako/data/core-defaults.yaml``) and how containerfiles/templates ship via
:mod:`importlib.resources`.

This module is the THIN reader those plugins call from their ``descriptor`` /
``default_common`` properties.  It builds the descriptor from the file so the
in-code descriptor is no longer hand-written per plugin.

Split (documented in each YAML header too):

* DECLARATIVE — everything in the file: the binding ORIGIN selectors (which
  detected install field supplies the host source — a declarative string, NOT a
  probed value), box-side destinations, the mode/operations grammar, settings
  routing, container env, cred-file lifecycle, host_prep/init_dirs, and the
  agent-common box_dests.  Box-side destinations under the guest home are written
  as ``$GUEST_HOME`` EXPRESSIONS (e.g. ``$GUEST_HOME/.local/bin/claude``) that
  this loader expands from the single :data:`~kanibako.settings.settings_resolve.GUEST_HOME`
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

from kanibako.settings.agent_config import (
    agent_category_root_ref,
    is_self_resolving,
    root_relative_source,
)
from kanibako.settings.core_defaults import add_bind
from kanibako.settings.settings_keyspace import (
    ACCESS_TIERS,
    is_terminal_category_tail,
)
from kanibako.settings.settings_resolve import GUEST_HOME, SettingsError
from kanibako.targets.base import (
    AccessRealization,
    AccessTierRow,
    BindArm,
    BindKind,
    Binding,
    BindScope,
    Cadence,
    CategoryBindDefaults,
    Channel,
    CredFileSpec,
    HostSrcOrigin,
    Operation,
    PersonaSpec,
    PluginDescriptor,
    SettingArg,
)


def _expand(value: str) -> str:
    """Expand a ``$GUEST_HOME`` prefix in a box-side path expression.

    The defaults files write every in-box destination as a ``$GUEST_HOME``
    expression so the guest-home literal lives in exactly one place (the
    :data:`~kanibako.settings.settings_resolve.GUEST_HOME` constant, single SoT).  Only the
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


def _build_access_row(
    tier: str, raw: dict[str, Any] | None, *, channel: Channel, source: str = "",
) -> AccessTierRow:
    """Build ONE :class:`AccessTierRow` from a declarative ``tiers.<tier>`` block.

    On the FLAG channel an EMPTY/absent body is the legal "emit nothing,
    deliberately" row (a default-safe harness's ``restricted``).  A field
    belonging to the OTHER channel is REFUSED rather than ignored: a ``flag:``
    under an ENV-channel descriptor would never be emitted, and a permission row
    that silently does nothing is precisely the failure this axis cannot afford.

    ⚑ On the ENV channel an empty row is REFUSED, not accepted.  Emitting nothing
    there leaves the harness's env var UNSET, and for the agents this channel
    exists for the unset default is the PERMISSIVE one (goose: no ``GOOSE_MODE``
    ⇒ ``auto`` ⇒ the full bypass).  So a DECLARED-but-empty ENV row would silently
    deliver ``full`` under another tier's name — the exact substitution the
    missing-row rule refuses to make.  A harness that cannot realize a tier OMITS
    the row (declaration, refused loudly at launch); declaring one obliges it to
    carry a value.  This is the invariant :class:`AccessTierRow` documents, now
    ENFORCED where the declaration is read rather than trusted.

    *source* names the defaults file for the message (the descriptor loader's
    ``filename``), so a refusal points at the plugin that has to be fixed.
    """
    body = raw or {}
    where = f" ({source})" if source else ""
    unknown = set(body) - {"flag", "env_value"}
    if unknown:
        raise SettingsError(
            f"access_realization.tiers.{tier}{where} declares unknown field(s) "
            f"{sorted(unknown)}; a tier row carries only 'flag' (FLAG channel) "
            f"or 'env_value' (ENV channel)."
        )
    if channel is Channel.FLAG and body.get("env_value"):
        raise SettingsError(
            f"access_realization.tiers.{tier}{where} sets 'env_value' but the "
            f"descriptor's channel is 'flag' — the value would never be emitted."
        )
    if channel is Channel.ENV and body.get("flag"):
        raise SettingsError(
            f"access_realization.tiers.{tier}{where} sets 'flag' but the descriptor's "
            f"channel is 'env' — the flag would never be emitted."
        )
    if channel is Channel.ENV and not body.get("env_value"):
        raise SettingsError(
            f"access_realization.tiers.{tier}{where} is DECLARED but empty on the 'env' "
            f"channel: it would set no variable, and an unset permission "
            f"variable is the harness's own default — which on this channel is "
            f"the PERMISSIVE one. A tier this harness cannot realize must OMIT "
            f"its row (the launch then refuses it by name); a declared row must "
            f"carry an 'env_value'."
        )
    return AccessTierRow(
        flag=tuple(body.get("flag", ())),
        env_value=body.get("env_value", ""),
    )


def _build_access_realization(
    raw: dict[str, Any] | None, *, source: str = "",
) -> AccessRealization | None:
    """Build the harness's :class:`AccessRealization` from its declarative block.

    ⚑ R-41: the block is now PER-TIER ROWS under ``tiers:`` (``restricted`` /
    ``editing`` / ``full``), not the old two-polarity ``flag``/``secure_flag`` +
    ``env_value``/``secure_env_value`` pair.  A tier the plugin OMITS is one this
    harness CANNOT render — the launch refuses that tier by name rather than
    substituting a neighbouring one (goose has no ``editing`` realization; see
    ``goose-defaults.yaml``).

    An unknown tier name is REFUSED: the tier set is closed by the spec, so a
    typo'd row must fail loudly at descriptor load instead of silently declaring
    nothing (which would read, at launch, as "this harness cannot do that").

    ⚑ The BLOCK's own fields get the same discipline the ROWS already get
    (:func:`_build_access_row`), for the same reason.  An unknown top-level field
    is REFUSED rather than ignored: the block is read field by field, so a typo'd
    name is simply never read.  And an ENV-channel block with no ``env_var`` is
    REFUSED: ``assemble_env`` emits a tier row only when the variable is named
    (``ar.channel is Channel.ENV and ar.env_var``), so ``env_ver:`` for
    ``env_var:`` would leave the harness's permission variable UNSET while the
    launch reports the requested tier — and on this channel the unset default is
    the PERMISSIVE one (goose: no ``GOOSE_MODE`` ⇒ ``auto`` ⇒ full bypass).  That
    is the same silent substitution the empty-ENV-row rule above refuses, reached
    one level up.

    *source* names the defaults file in every refusal (see
    :func:`_build_access_row`).
    """
    if not raw:
        return None
    where = f" ({source})" if source else ""
    unknown_fields = set(raw) - {"channel", "env_var", "tiers", "setting_key"}
    if unknown_fields:
        raise SettingsError(
            f"access_realization{where} declares unknown field(s) "
            f"{sorted(unknown_fields)}; the block carries only 'channel', "
            f"'env_var' (ENV channel), 'tiers' and 'setting_key'."
        )
    channel = Channel(raw["channel"])
    env_var = raw.get("env_var", "")
    if channel is Channel.ENV and not env_var:
        raise SettingsError(
            f"access_realization{where} declares \"channel: 'env'\" but names no "
            f"'env_var': every tier row would be assembled and then SKIPPED, "
            f"leaving the harness's permission variable UNSET — which on this "
            f"channel is the harness's own default, and that default is the "
            f"PERMISSIVE one (goose: no 'GOOSE_MODE' ⇒ 'auto'). The launch would "
            f"still report the requested tier. An 'env' channel realization must "
            f"name its 'env_var'."
        )
    tiers_raw = raw.get("tiers") or {}
    if not isinstance(tiers_raw, dict):
        raise SettingsError(
            f"access_realization.tiers{where} must be a mapping of tier name → row."
        )
    unknown_tiers = set(tiers_raw) - set(ACCESS_TIERS)
    if unknown_tiers:
        raise SettingsError(
            f"access_realization.tiers{where} declares unknown tier(s) "
            f"{sorted(unknown_tiers)}; the permission tiers are "
            f"{' | '.join(ACCESS_TIERS)} (spec §2d)."
        )
    rows = {
        tier: _build_access_row(
            tier, tiers_raw[tier], channel=channel, source=source,
        )
        for tier in ACCESS_TIERS
        if tier in tiers_raw
    }
    return AccessRealization(
        channel=channel,
        env_var=env_var,
        restricted=rows.get("restricted"),
        editing=rows.get("editing"),
        full=rows.get("full"),
        setting_key=raw.get("setting_key", ""),
    )


def _build_setting_arg(entry: dict[str, Any], *, source: str = "") -> SettingArg:
    """Build ONE :class:`SettingArg` from a declarative ``settings:`` entry.

    Same load-time discipline as :func:`_build_access_realization`, and for the
    same reason.  An unknown top-level field is REFUSED rather than ignored (the
    entry is read field by field, so a typo'd name is never read), and an
    ENV-channel entry that names no ``env_var`` is REFUSED because
    ``assemble_env`` emits only when the variable is named (``s.channel is
    Channel.ENV and s.env_var``): the value would be resolved through the whole
    cascade and then dropped, silently.  For claude's ``endpoint`` that means a
    persona launching against the harness's DEFAULT endpoint while every
    preflight reports the configured one.

    ⚑ The FLAG channel gets the SAME rule, and its failure is WORSE than a drop.
    ``assemble_argv`` emits ``flag + [value]`` unconditionally, so an entry whose
    ``flag`` is absent or empty extends by NOTHING and then appends the value —
    the value lands as a BARE POSITIONAL.  For claude that positional is the
    initial PROMPT (``claude [options] [command] [prompt]``, verified against the
    installed binary), so a malformed descriptor turns a setting's value into the
    text the agent is asked to act on; for a harness whose bare positional is a
    SUBCOMMAND (goose: ``goose [COMMAND]``) it is instead a hard launch failure
    with an unrelated-looking message.  Either way the entry never does what it
    declares.  The typo route (``flga:``) is already covered by the unknown-field
    guard above; this covers the OMISSION route, which that guard cannot see.

    ⚑ Do NOT read this rule across onto :class:`AccessTierRow`, whose empty
    ``flag`` is MEANINGFUL and must stay legal: a tier row emits its flag and
    NOTHING ELSE, so ``()`` there is the documented "emit nothing, deliberately"
    realization (claude/codex ``restricted``).  A :class:`SettingArg` always
    appends its value, so for IT an empty flag has no such reading — there is no
    way to spell "emit nothing" with a setting arg, only ways to misplace the
    value.

    *source* names the defaults file in the refusal, so a plugin author is
    pointed at the file to fix (see :func:`_build_access_row`).
    """
    where = f" ({source})" if source else ""
    named = entry.get("setting_key", "<unnamed>")
    unknown = set(entry) - {"setting_key", "channel", "flag", "env_var"}
    if unknown:
        raise SettingsError(
            f"settings entry {named!r}{where} declares unknown field(s) "
            f"{sorted(unknown)}; a setting arg carries only 'setting_key', "
            f"'channel', 'flag' (FLAG channel) and 'env_var' (ENV channel)."
        )
    channel = Channel(entry["channel"])
    env_var = entry.get("env_var", "")
    if channel is Channel.ENV and not env_var:
        raise SettingsError(
            f"settings entry {named!r}{where} declares \"channel: 'env'\" but "
            f"names no 'env_var': the value would be resolved and then SKIPPED, "
            f"so the setting silently never reaches the box while the launch "
            f"reports it. An 'env' channel setting must name its 'env_var'."
        )
    flag = tuple(entry.get("flag", ()))
    if channel is Channel.FLAG and not flag:
        raise SettingsError(
            f"settings entry {named!r}{where} declares \"channel: 'flag'\" but "
            f"names no 'flag': assembly emits 'flag + [value]', so an empty "
            f"'flag' appends the VALUE ON ITS OWN as a BARE POSITIONAL. For "
            f"claude that positional is the initial PROMPT, so the setting's "
            f"value would become the text the agent is asked to act on. A "
            f"'flag' channel setting must name its 'flag'."
        )
    return SettingArg(
        setting_key=entry["setting_key"],
        channel=channel,
        flag=flag,
        env_var=env_var,
    )


def _build_persona(raw: dict[str, Any] | None) -> PersonaSpec | None:
    """Build the harness-specific :class:`PersonaSpec` from its block (or None).

    Absent ``persona:`` → ``None`` → the preflight falls back to claude-style ENV
    endpoint delivery + the ``ANTHROPIC_AUTH_TOKEN`` token var (byte-identical).

    Every field falls back to the :class:`PersonaSpec` field default when the block
    omits it — ``model_required`` included, whose default is ``False``: a harness
    that does not DECLARE the model veto does not get it, so an absent model means
    "this persona needs none".
    """
    if not raw:
        return None
    pin_raw = raw.get("provider_pin") or {}
    provider_pin = tuple(sorted((str(k), str(v)) for k, v in pin_raw.items()))
    return PersonaSpec(
        token_var=raw.get("token_var", ""),
        endpoint_delivery=raw.get("endpoint_delivery", "env"),
        wire_api=raw.get("wire_api", "responses"),
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

    ⚑ The RETIRED key ``safe_bypass:`` is REFUSED by name.  Descriptor keys are
    read individually (``desc.get(...)``), so an unrecognized one is simply not
    read — and for THIS key that silence is dangerous rather than harmless: the
    descriptor would load with NO ``access_realization``, the launch's
    un-rendered-tier gate would have nothing to check, and the agent would run
    with no permission emission at all.  On the ENV channel that IS the bypass
    (goose: an unset ``GOOSE_MODE`` means ``auto``).  A renamed key must fail
    loudly and name its replacement, never degrade quietly into permissive.
    """
    doc = _load_doc(package, filename)
    desc = doc.get("descriptor", {})

    if "safe_bypass" in desc:
        raise SettingsError(
            f"{filename}: descriptor declares the RETIRED key 'safe_bypass'. "
            f"The access-tier realization block is now 'access_realization' "
            f"(same shape). Rename the key: left as-is it is an unknown "
            f"descriptor key, so this agent would load with NO permission "
            f"realization and launch with none emitted — which on the 'env' "
            f"channel is the harness's own PERMISSIVE default."
        )

    return PluginDescriptor(
        command=tuple(desc["command"]),
        bindings=tuple(_build_binding(b, package) for b in desc.get("bindings", [])),
        mode={k: tuple(v) for k, v in desc.get("mode", {}).items()},
        operations={
            k: Operation(tuple(v["fragment"]))
            for k, v in desc.get("operations", {}).items()
        },
        access_realization=_build_access_realization(
            desc.get("access_realization"), source=filename,
        ),
        settings=tuple(
            _build_setting_arg(s, source=filename) for s in desc.get("settings", [])
        ),
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


def load_category_binds(
    package: str, filename: str, agent: str
) -> CategoryBindDefaults:
    """Build a plugin's AGENT-scope ``@``-ref-sourced category BINDS from its file.

    Each entry in the file's ``category_binds:`` section declares one agent-scope
    category default whose HOST SOURCE is an ``@``-ref (``meta_ref``) — the mirror
    of :mod:`kanibako.settings.core_defaults`'s ``meta_ref`` bind shape, at AGENT
    scope.  *agent* is the declaring plugin's own name; the agent tier is
    DISCRIMINATED (spec §2d / §0 — there is NO bare ``agent.<key>``), so every key
    is built discriminated HERE rather than re-rooted downstream.  ``start.py``
    folds this table into ``default_categories`` alongside :func:`load_common`.

    ⚑⚑ **ONE SHAPE FOR EVERY CATEGORY since 2026-08-08c** (the ``bindings`` arms
    flipped first, 2026-08-06c — R-5/R-10/R-11).

    Every entry lands in a TERMINAL key — ``agent.<agent>.bindings.{ro,rw}`` for an
    ARMED category, ``agent.<agent>.<category>`` for ``common`` / ``caches`` /
    ``seeded`` / ``synced`` — whose whole value is a dest-keyed
    :data:`~kanibako.targets.base.BindArm`, ``{box_dest: (meta_ref[, "ro"])}``.
    The box DESTINATION is the KEY and the entry NAME is GONE.  All ENTRIES of one
    category live under ONE key, so the file's rows are GROUPED here rather than
    emitted one key apiece.

    Each map is built through :func:`kanibako.settings.core_defaults.add_bind`, the
    SAME constructor every core floor producer goes through — so the arm key
    spelling, the R-11 destination normalization and the act-once refusal are
    written ONCE for core and for plugins rather than re-typed here.  ⚑ Normalizing
    the destination is NOT cosmetic, though not for the obvious reason: two
    spellings do NOT reach podman as two mounts. ``commands.start``'s floor merge
    dedupes on these keys BEFORE anything parses them, so ``~/x`` and
    ``/home/agent/x`` survive it as two entries; both then resolve to the same
    guest dest and ``reconcile_categories`` raises ``binding_vs_binding``. The
    cost is the FAILED OVERRIDE that precedes the error: a value written at the
    canonical spelling does not REPLACE an unnormalized entry, it becomes a
    SECOND one.

    A ``key:`` on ANY category row is **REFUSED**, not ignored.  Dropping
    it silently would let a plugin written against the retired contract keep
    loading while quietly producing a DIFFERENT key than it declared — the worst of
    the three outcomes.  (The same retired spelling arriving as a dotted floor key
    is refused a second time, by name, in
    :func:`kanibako.settings.settings_assemble._insert_dotted`.)

    The value's element 0 is the RAW ``@``-ref STRING (e.g. a ``"@system.*"``
    source key); the launch category cascade folds it into the floor and ``expand``
    resolves it to the referenced path — so a plugin declares a bind to a shared
    source WITHOUT any per-harness path knowledge in core (spec §2d).

    ``box_dest`` is a ``~`` / ``$GUEST_HOME`` expression.  ``$GUEST_HOME`` is
    expanded here; a leading ``~`` survives that expansion and is then
    canonicalized to the guest home by ``normalize_bind_dest`` — a dest is a GUEST
    path, so it resolves the same on every host (R-11).
    ``ro: true`` emits the explicit ``"ro"`` mount option; otherwise the option is
    omitted and reconcile falls back to the category default.  Returns ``{}`` when
    the file declares no category binds.

    ⚑ NO ROOT IS SUPPLIED HERE, and a bare-relative ``meta_ref`` is REFUSED.  This
    section declares CONCRETE entries, which take no root at
    any scope (spec §2a's DECLARATION-ROOT table covers the ABSTRACT categories
    only, and it applies at the AUTHORING seam — :func:`load_common` — not here)
    — so a relative source is a plugin DEFECT that would silently resolve
    against the process CWD, not a shorthand.  The refusal names the file and the
    DESTINATION that identifies the entry.

    ⚑ The declared *category* must be a TERMINAL category key
    (:func:`~kanibako.settings.settings_keyspace.is_terminal_category_tail`).
    Since 2026-08-08c that is every bind-shaped category, so the test no longer
    SELECTS between two shapes — it REFUSES a category that is not one, which is
    the closed-keyspace rule (spec §0) rather than a fallback.
    """
    # ⚑ The ONE dest-keyed map constructor (disk-store rework R-3/R-6/R-11), reused
    # rather than re-typed: core's floor producers and a plugin's declarations must
    # emit ONE shape, and a second hand-rolled copy here is exactly how the two
    # would drift.
    binds: CategoryBindDefaults = {}
    for entry in _load_doc(package, filename).get("category_binds", []):
        category = str(entry["category"])
        segments = tuple(category.split("."))
        if not is_terminal_category_tail(segments):
            raise SettingsError(
                f"{filename}: category_bind declares category {category!r}, "
                f"which is not a declared §2a category key. A bindings entry is "
                f"declared per ARM ('bindings.ro' / 'bindings.rw'); the other "
                f"bind-shaped categories are 'caches' / 'seeded' / 'common' / "
                f"'synced' (spec §0 — the keyspace is CLOSED)."
            )
        box_dest = _expand(entry["box_dest"])
        host_src = entry["meta_ref"]
        options = "ro" if entry.get("ro", False) else None

        category_key = f"agent.{agent}.{category}"
        if "key" in entry:
            raise SettingsError(
                f"{filename}: category_bind under {category_key!r} declares an "
                f"entry name 'key: {entry['key']}', which is the RETIRED "
                f"name-keyed shape. A bind-shaped category is a TERMINAL key "
                f"whose value is keyed by box DESTINATION ({box_dest!r} here); "
                f"the entry name was dropped (bindings 2026-08-06c, the other "
                f"four 2026-08-08c; spec §2a, R-5/R-10). "
                f"Delete the 'key:' line — left in place this entry would "
                f"load but bind under a different key than it names."
            )
        ident = f"{category_key!r} entry at {box_dest!r}"

        if not is_self_resolving(host_src):
            raise SettingsError(
                f"{filename}: category_bind {ident} declares a bare-relative "
                f"host source {host_src!r}; a source must fully resolve on its "
                "own (absolute, ~, $var or an @-ref) — bindings take no root at "
                "any scope (spec §2a L474-486)"
            )

        try:
            add_bind(
                binds, category, box_dest, host_src, options,
                scope=f"agent.{agent}",
            )
        except ValueError as exc:
            # ``add_bind`` owns the act-once invariant and names the category and
            # the destination; only the FILE is missing from its message.
            raise SettingsError(f"{filename}: {exc}") from exc
    return binds


def load_common(package: str, filename: str, agent: str) -> "dict[str, BindArm]":
    """Build a plugin's AGENT-scope ``default_common`` table from its defaults file.

    Returns the ONE discriminated TERMINAL key ``agent.<agent>.common`` mapped to
    its whole dest-keyed :data:`~kanibako.targets.base.BindArm`
    ``{box_dest: (host_src[, options])}`` (spec §2a — structured, NOT a
    colon-joined string).  *agent* is the declaring plugin's own name; the agent
    tier is DISCRIMINATED (§2d / §0 — there is NO bare ``agent.<key>``).
    ``box_dest`` is a ``$GUEST_HOME`` expression expanded here and then normalized
    by :func:`~kanibako.settings.core_defaults.add_bind`, because it is the KEY.
    Returns ``{}`` when the file declares no ``common`` entries — an EMPTY table,
    not a key holding an empty map.

    ROOTED AT DECLARATION (spec §2a).  An author writes a bare leaf
    (``plugins``); what is STORED is the full self-resolving
    ``@meta.agent.<agent>.path/common/plugins``, so no later layer prepends
    anything (§2a — a source must fully resolve on its own).  A source
    that is ALREADY self-resolving (absolute / ``~`` / ``$var`` / ``@``-ref) is
    stored VERBATIM (§2a); :func:`~kanibako.settings.agent_config.
    root_relative_source` owns that rule and
    :func:`~kanibako.settings.agent_config.agent_category_root_ref` owns the layout.

    ⚑ **THE ENTRY NAME IS GONE (2026-08-08c).**  ``common`` is a TERMINAL
    dest-keyed key, so the file's former ``key:`` leaf names nothing and is
    REFUSED rather than ignored — the same refusal, for the same reason, as
    :func:`load_category_binds`.  What survives is the pairing the old ``key`` /
    ``host_src`` independence existed to protect: ``host_src`` is still the path
    leaf that gets rooted, and it is still independent of ``box_dest``, so a
    user's override can repoint the source without moving the destination.
    ⚑ The store DIRNAME a persona's symlink shim needs used to be read off the
    key; it now comes off the rooted ``host_src`` — see
    :func:`~kanibako.settings.agent_representation.harness_common_leaf`, the ONE
    place that rule is written.
    """
    root_ref = agent_category_root_ref(agent, "common")
    binds: "dict[str, BindArm]" = {}
    for entry in _load_doc(package, filename).get("common", []):
        if "key" in entry:
            raise SettingsError(
                f"{filename}: common entry declares an entry name "
                f"'key: {entry['key']}', which is the RETIRED name-keyed shape. "
                f"'agent.{agent}.common' is a TERMINAL key whose value is keyed "
                f"by box DESTINATION; the entry name was dropped 2026-08-08c "
                f"(spec §2a). Delete the 'key:' line."
            )
        host_src = root_relative_source(entry["host_src"], root_ref)
        try:
            add_bind(
                binds, "common", _expand(entry["box_dest"]), host_src,
                entry.get("options"), scope=f"agent.{agent}",
            )
        except ValueError as exc:
            raise SettingsError(f"{filename}: {exc}") from exc
    return binds
