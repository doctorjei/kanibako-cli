"""YAML config loading, writing, defaults, and merge logic."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

from kanibako.settings.config_io import dump_doc, load_doc


# ---------------------------------------------------------------------------
# Defaults (match the old kanibako.rc values)
# ---------------------------------------------------------------------------

# Per-box construct-time metadata + box-tier settings cascade file (TARGET §2c meta.box.*)
BOX_META_FILE = "settings.yaml"

# Shared boolean truth tables: used by the typed `config set` writer
# (config_interface) AND the box.meta writer so both round-trip identically.
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


def coerce_bool(value: object) -> bool | None:
    """Coerce a config value to a real bool using the shared truth table.

    Returns the bool, or None if *value* is not a recognized bool literal.
    Already-bool values pass through. Used by the typed `config set` writer
    (config_interface) AND the box.meta writer so both round-trip identically.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
    return None


_DEFAULTS = {
    "paths_project_toml": BOX_META_FILE,
    "box_image": "ghcr.io/doctorjei/kanibako-oci:latest",
    "box_shell": "",
}


@dataclass
class KanibakoConfig:
    """Merged configuration (hardcoded defaults < kanibako_config.yaml < settings.yaml < CLI)."""

    paths_project_toml: str = _DEFAULTS["paths_project_toml"]
    box_image: str = _DEFAULTS["box_image"]
    # ⚑ ``box_agent_name`` is GONE (P7, spec §2b): ``box.agent_name`` is
    # RETIRED and a box selects its agent with the REQUEST ``pref.system.agent``
    # (§2h), resolved off the launch snapshot by :mod:`kanibako.settings.agent_select`.
    # There is no flat-scalar agent field any more — the selection is a KEY.
    box_shell: str = _DEFAULTS["box_shell"]
    box_share_images: bool = False
    # Bootstrap PATH set-values keyed by full dotted name — the MERGED Layer-1
    # ``config.<leaf>`` foundation keys (from the ``[config]`` table) AND the
    # Layer-2 ``system.<leaf>`` path settings (from the ``[system]`` table),
    # read from ``kanibako_config.yaml``.  Config-file-only (never supplied by
    # project/workset configs).
    config_paths: dict[str, str] = field(default_factory=dict)


def _flatten_toml(data: dict, prefix: str = "") -> dict[str, object]:
    """Flatten nested config dict into underscore-joined keys.

    ``{"paths": {"boxes": "x"}}`` → ``{"paths_boxes": "x"}``
    Booleans are preserved; ``None`` (YAML ``null``/empty) is preserved as the
    "reset to built-in default" sentinel; other scalars are stringified.
    """
    out: dict[str, object] = {}
    for k, v in data.items():
        key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_toml(v, key))
        elif isinstance(v, bool):
            out[key] = v
        elif v is None:
            out[key] = None
        else:
            out[key] = str(v)
    return out


def config_file_path(config_home: Path) -> Path:
    """Return the path to the bootstrap config file ``kanibako_config.yaml``.

    Location: ``$XDG_CONFIG_HOME/kanibako_config.yaml``.  CLEAN BREAK (JC-1): the
    old ``kanibako.yaml`` name is NOT read-compat (pre-release; Jei's own data).
    """
    return config_home / "kanibako_config.yaml"


def config_base_path() -> Path:
    """Return the machine-wide CONFIG base file (``/etc/kanibako/config_base.yaml``).

    The least-specific layer of the bootstrap-PATH file set: a site admin supplies
    overridable defaults that the user's ``~/.config/kanibako_config.yaml`` can
    still beat.  Missing file → treated as an empty level.
    """
    return Path("/etc/kanibako/config_base.yaml")


def settings_base_path() -> Path:
    """Return the machine-wide SETTINGS base file (``/etc/kanibako/settings_base.yaml``).

    The LEAST-specific (bottom) layer of the SETTINGS (behavior) cascade — below
    every scope (``system``/``agent``/``workset``/``box``): a site admin supplies
    overridable behavior defaults that any scope can still beat.  Missing file →
    treated as an empty level (so its absence preserves current behavior).
    """
    return Path("/etc/kanibako/settings_base.yaml")


def _present_scalar_fields(path: Path) -> dict[str, object]:
    """Parse a config file and return ONLY the scalar/bool fields actually
    present in it, as a field-name → value mapping.

    A value of ``None`` (YAML ``null``/``~``/empty ``foo:``) is preserved as the
    "reset to built-in default" sentinel; callers must distinguish it from an
    absent key (which simply won't appear in the returned dict).

    The dict field (``config_paths``) is NOT included here; it keeps its own
    dedicated parsing/merge logic.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    # Drop the sections handled by dedicated logic so they don't leak into the
    # scalar field overlay.  The [config] (Layer-1) + [system] (Layer-2) tables
    # are the bootstrap-PATH tier (handled by load_config's config_paths
    # extraction), not flat scalar fields.
    data.pop("config", None)
    data.pop("system", None)
    flat = _flatten_toml(data)
    valid_keys = {fld.name for fld in fields(KanibakoConfig)}
    present: dict[str, object] = {}
    for k, v in flat.items():
        if k in valid_keys:
            present[k] = v
    return present


def load_config(path: Path) -> KanibakoConfig:
    """Read a single config file and return a KanibakoConfig with defaults filled in."""
    cfg = KanibakoConfig()
    if path.exists():
        data = load_doc(path)
        # Extract the bootstrap-PATH tables: the Layer-1 ``[config]`` foundation
        # keys (``config.<leaf>``) and the Layer-2 ``[system]`` path settings
        # (``system.<leaf>``), merged into one ``config_paths`` set keyed by full
        # dotted name.  Each table is flattened so nested sub-keys (e.g.
        # ``system.channels.common``) become dotted keys; scalar leaves
        # (e.g. ``config.data``) stay flat.
        merged: dict[str, str] = {}
        config_tbl = data.get("config", {})
        if isinstance(config_tbl, dict):
            merged.update(_flatten_dotted(config_tbl, "config"))
        system_tbl = data.get("system", {})
        if isinstance(system_tbl, dict):
            merged.update(_flatten_dotted(system_tbl, "system"))
        cfg.config_paths = merged
        # Scalar/bool fields: a present key sets the field; a ``None`` value
        # (YAML null/empty) resets it to the built-in default.
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(KanibakoConfig(), k))
            else:
                setattr(cfg, k, v)
    return cfg


#: The three box-scope SCALAR keys the merged loader resolves through the
#: KEYSPACE (B6, R-11a(a)): dotted key → the flat ``KanibakoConfig`` field it
#: lands on. ``box.shell`` rides the same resolve (it lives on the same object
#: and the same ``box:`` tables — consumer-map risk 4).
_BOX_SCALAR_FIELDS: dict[str, str] = {
    "box.image": "box_image",
    "box.share_images": "box_share_images",
    "box.shell": "box_shell",
}


def _resolve_box_scalars(
    global_path: Path,
    floor_values: "dict[str, object]",
    *,
    workset_path: Path | None,
    box_path: Path | None,
    cli_overrides: "dict[str, object] | None",
) -> dict[str, object]:
    """Resolve the three box scalars (:data:`_BOX_SCALAR_FIELDS`) through the
    KEYSPACE — the ONE resolve behind ``load_merged_config`` (B6, option (b)).

    A focused, AGENT-LESS ``build_launch_snapshot`` (the ``"general"`` slot —
    the proven ``_effective_bootstrap`` shape, so ``kanibako shell`` and every
    box-less caller resolve without an agent) over the real cascade files::

        floor(kanibako_config.yaml [box]) < /etc settings_base.yaml < system
        (global/settings.yaml) < workset < box < CLI level

    * **The stored system default is MAPPED, not stranded** (consumer-map risk
      1): every install's ``kanibako_config.yaml`` carries a ``[box]`` table
      (written at init), which the settings cascade does not read — its values
      enter here as the FLOOR (*floor_values*, captured from the ``load_config``
      read of *global_path* BEFORE any overlay), so they keep beating the
      built-in defaults and keep losing to every settings file, exactly the flat
      loader's precedence. A ``box:`` table in ``global/settings.yaml`` — where
      ``kanibako system config set box.image=…`` has always written — now
      resolves too (it was silently stranded before B6).
    * **The CLI flags ride the §1A LEVEL**: *cli_overrides* (flat field names,
      the historical transport) are translated through the ONE builder
      :func:`~kanibako.settings.settings_cli_level.build_cli_level` and guarded
      inside ``build_launch_snapshot`` — not overlaid ad hoc.

    Returns ``{dotted key: resolved leaf}`` with ABSENT keys omitted (the caller
    falls back to the flat value, which owns the ``None``-reset / built-in
    default corner semantics). Lazy imports throughout: ``paths`` and
    ``settings_launch`` both import this module at module load.
    """
    from kanibako.settings.paths import load_system_config, host_xdg_map, xdg
    from kanibako.settings.settings_cli_level import build_cli_level
    from kanibako.settings.settings_launch import build_launch_snapshot
    from kanibako.settings.settings_resolve import ResolveCtx
    from kanibako.settings.settings_store import KeyStore

    # The system SETTINGS file (@config.settings = global/settings.yaml) — path
    # resolution, deliberately NOT load_std_paths (which materializes the store).
    # ⚑ Not literally mkdir-free: with XDG_RUNTIME_DIR unset, resolve_system_paths'
    # fallback CREATES its replacement runtime dir (once per process, cached) —
    # the single directory this call can make.
    system_path = load_system_config(
        global_path, data_home=xdg("XDG_DATA_HOME", ".local/share"),
        home=Path.home(),
    )["config.settings"]

    # The kanibako_config [box] tier as the FLOOR (risk 1). ``""`` entries are
    # dropped by the fold (absent ≡ no default) — the flat fallback then applies
    # the built-in default, preserving the ""-corner byte-identically.
    floor = dict(floor_values)

    overrides = cli_overrides or {}
    image_val = overrides.get("box_image")
    cli_level = build_cli_level(
        image=str(image_val) if image_val else None,
        share_images=bool(overrides.get("box_share_images", False)),
    )

    snapshot = build_launch_snapshot(
        agent_name="general",
        ctx=ResolveCtx(
            agent_name="general", workset_name=None,
            host_home=str(Path.home()), xdg=host_xdg_map(),
        ),
        system_path=system_path if system_path.exists() else None,
        agent_path=None,
        workset_path=workset_path,
        box_path=box_path,
        default_categories=floor,
        cli_level=cli_level,
    )

    resolved: dict[str, object] = {}
    for dotted in _BOX_SCALAR_FIELDS:
        node: object = snapshot
        for seg in dotted.split("."):
            if not isinstance(node, KeyStore):
                node = None
                break
            node = dict.get(node, seg)
        if node is not None:
            resolved[dotted] = node
    return resolved


def load_merged_config(
    global_path: Path,
    project_path: Path | None = None,
    *,
    workset_path: Path | None = None,
    cli_overrides: "dict[str, object] | None" = None,
) -> KanibakoConfig:
    """Load global config, overlay workset, project, then CLI overrides.

    Precedence: CLI flags > settings.yaml > workset config.yaml >
    kanibako_config.yaml (user) > hardcoded defaults.

    ⮕ **B6 (R-11a(a)): the box scalars are KEYSPACE-RESOLVED.** The three
    declared keys ``box.image`` / ``box.share_images`` / ``box.shell`` are no
    longer the flat overlay's product: :func:`_resolve_box_scalars` resolves
    them through the real cascade (base < system < workset < box < the §1A CLI
    level), with the ``kanibako_config.yaml [box]`` tier mapped as the floor, and
    the resolved values overwrite the flat fields on the returned object. Every
    caller — the launch, ``kanibako shell`` (agent-less), and the box-less sites
    (``rig``/``diagnose``/``setup``/``baseline``, which pass no project) — reads
    the SAME resolve through the same fields, so there is ONE live source. The
    flat overlay walk below still runs: it owns ``paths_project_toml`` and the
    corner semantics (present-``None`` reset; ``""``) the resolve falls back to.

    The old machine-wide ``/etc/kanibako/kanibako.yaml`` third file is DELETED
    (spec §2 — the admin authority is exactly the ``config_base.yaml`` /
    ``settings_base.yaml`` base tiers, resolved on the PATH side; this scalar
    loader starts from the built-in defaults).
    """
    defaults = KanibakoConfig()

    def _overlay_scalars(cfg: KanibakoConfig, path: Path) -> None:
        """Overlay one file layer's PRESENT scalar/bool fields onto *cfg*.

        Presence-based: a key absent from this layer leaves the underlying value
        untouched; a present key with a ``None`` value (YAML null/empty) resets
        the field to its built-in default; any other present value (including
        ``""``) sets the field.  ``config_paths`` is config-file-only and handled
        separately, so it never appears here.
        """
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(defaults, k))
            else:
                setattr(cfg, k, v)

    # Start from the user global config (the least-specific FILE source now that
    # the machine third-file is deleted), then overlay the workset + project
    # layers so the most-specific present value wins (null/empty resets).
    cfg = load_config(global_path)
    # The kanibako_config [box] tier for the resolve's FLOOR — captured BEFORE
    # the overlays, so a workset/box value cannot masquerade as the system-stored
    # default (it enters the resolve at its OWN tier instead).
    floor_values: dict[str, object] = {
        dotted: getattr(cfg, field_name)
        for dotted, field_name in _BOX_SCALAR_FIELDS.items()
    }
    if workset_path and workset_path.exists():
        _overlay_scalars(cfg, workset_path)
    if project_path and project_path.exists():
        _overlay_scalars(cfg, project_path)
    if cli_overrides:
        valid_keys = {fld.name for fld in fields(cfg)}
        for k, v in cli_overrides.items():
            if k in valid_keys:
                setattr(cfg, k, v)

    # KEYSPACE resolve for the box scalars (B6): the resolved value wins; an
    # ABSENT/None resolve keeps the flat value (which owns the None-reset / ""
    # corner semantics — see _resolve_box_scalars).
    resolved = _resolve_box_scalars(
        global_path, floor_values,
        workset_path=workset_path, box_path=project_path,
        cli_overrides=cli_overrides,
    )
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        if dotted not in resolved:
            continue
        value = resolved[dotted]
        if field_name == "box_share_images":
            coerced = coerce_bool(value)
            setattr(cfg, field_name, coerced if coerced is not None else bool(value))
        else:
            setattr(cfg, field_name, str(value))
    return cfg


def write_global_config(path: Path, cfg: KanibakoConfig | None = None) -> None:
    """Write a YAML config file with the structured layout.

    If *cfg* is None, writes defaults.
    """
    if cfg is None:
        cfg = KanibakoConfig()
    # Bootstrap PATH tier, written at the DEFAULT expressions in TWO tables:
    #   * ``[config]`` — the Layer-1 foundation (the 5 ``config.*`` keys; spec §1)
    #   * ``[system]`` — the Layer-2 ``system.*`` path SETTINGS (channelroot/
    #     template/canon/backup/cache/runtime + the channels skeleton; spec §2g)
    # Kept in lock-step with paths.CONFIG_PATH_DEFAULTS / SYSTEM_PATH_DEFAULTS;
    # the resolver fills in any omitted key, so only the most commonly-tuned
    # roots are emitted (the derived files/dirs resolve from these).
    data: dict = {
        "config": {
            "data": "$XDG_DATA_HOME/kanibako",
            "settings": "@config.data/global/settings.yaml",
            "agents": "@config.data/agents",
            "primary_workset": "@config.data/primary_workset",
            "registry": "@config.data/global/registry.yaml",
            "journal": "@config.data/global/journal.yaml",
        },
        "system": {
            "backup": "@config.data/backup",
            "channelroot": "@config.data/channels",
            # M-11: ``base_template`` → ``template`` (default re-pointed
            # ``global/base_template`` → ``global/template``), plus the new
            # ``canon`` contribution root (spec §2g). ⚑ These literals DUPLICATE
            # ``paths.SYSTEM_PATH_DEFAULTS`` — a single-source violation the file
            # header already flags ("kept in lock-step"); every edit here needs the
            # matching edit there.
            "template": "@config.data/global/template",
            "canon": "@config.data/global/canon",
            "cache": "$XDG_CACHE_HOME/kanibako",
            "runtime": "$XDG_RUNTIME_DIR/kanibako",
        },
        # ⚑ NO ``agent_name`` row (P7): ``box.agent_name`` is RETIRED (§2b), and
        # writing a BOX key into the CONFIG file was wrong even while it existed —
        # nothing ever read it back from here. Stale copies in existing
        # ``kanibako_config.yaml`` files are documentation-only (migration M-4).
        "box": {
            "image": cfg.box_image,
            "share_images": cfg.box_share_images,
        },
    }
    dump_doc(path, data)


def write_project_config(path: Path, image: str) -> None:
    """Write or update a settings.yaml with the given image."""
    write_project_config_key(path, "box_image", image)


def persist_creation_flags(
    box_settings_path: Path,
    *,
    materializing: bool,
    image: str | None = None,
    share_images: bool | None = None,
) -> None:
    """The §1A **CREATE EXCEPTION** — the ONE gate through which a shadowing CLI
    flag's value ever PERSISTS (R-11a; materialization ruling 2026-08-02).

    Spec §1A: a flag applies to ONE launch and NEVER mutates an EXISTING stored
    value — *"at box CREATION only, a shadowing flag's value PERSISTS — it
    INITIALIZES the box's stored config."* Launch-MATERIALIZATION counts as
    creation (Jei, 2026-08-02): the one signal is *materializing* — "is this box
    being materialized by THIS invocation?" — which ``kanibako create`` and the
    launch path both read off their resolve's ``proj.is_new``. Every caller
    routes through THIS gate; there is no per-path persist logic (the former
    ``start._persist_image_override`` and its deferred-arm replay collapsed into
    it), so ``create``, a launch that materializes a registered-but-unbuilt box,
    and a plain ``start --image`` on an EXISTING box (strictly ephemeral) all
    get the rule from one place.

    Only EXPLICITLY-GIVEN flag values persist: an absent flag (``None``; ``""``
    for *image* — absent ≠ ``""``) writes NOTHING, so a no-flag create bakes NO
    default into the box tier and the box resolves the live cascade (single
    source of truth; the stored default stays at its own tier). No flags → no
    write at all — no empty settings.yaml is materialized (the
    :func:`write_box_enable_vault` rule).

    *box_settings_path* is the BOX-TIER settings file from
    ``box_workset_settings_paths`` — the same file ``box set box.image=…`` writes
    and the launch cascade reads as the box tier (M-8). *share_images* is a real
    bool or ``None`` (absent); it is written as a bool, matching the
    ``KEY_TYPES`` coercion ``config set box.share_images`` applies.
    """
    if not materializing:
        return
    updates: dict[str, object] = {}
    if image:
        updates["image"] = image
    if share_images is not None:
        updates["share_images"] = bool(share_images)
    if not updates:
        return
    data = load_doc(box_settings_path)
    sec = data.get("box")
    if not isinstance(sec, dict):
        sec = {}
        data["box"] = sec
    sec.update(updates)
    dump_doc(box_settings_path, data)


def write_box_enable_vault(path: Path, enable_vault: bool = True) -> None:
    """Sparsely persist the box-scope ``box.enable_vault`` key at *path*.

    The single writer for ``box.enable_vault`` at box create/move time (P8b —
    extracted from the retired ``write_project_meta`` identity write so create no
    longer emits a ``project:``/``resolved:`` section: box identity lives in the
    registries (``box_resolve``), not on disk — Option A).  Sparse, matching
    ``config set box.enable_vault``:

    * ``enable_vault`` explicitly ``False`` → write ``box.enable_vault = False``
      into the ``box:`` table (created + merged beside ``box.image``);
    * the default ``True`` → write NOTHING, and DROP any stale
      ``box.enable_vault`` override (an empty ``box:`` table is never
      materialized, and a would-be no-op leaves the file untouched — so a
      default-vault primary/named box gets no settings.yaml written here).

    Paired reader: :func:`read_box_enable_vault`.
    """
    existing = load_doc(path)
    ev = coerce_bool(enable_vault)
    if ev is False:
        existing.setdefault("box", {})["enable_vault"] = False
        dump_doc(path, existing)
        return
    # Default (True): only rewrite when there is a stale override to drop —
    # otherwise leave the file exactly as-is (no empty file materialized).
    box_sec = existing.get("box")
    if isinstance(box_sec, dict) and "enable_vault" in box_sec:
        box_sec.pop("enable_vault", None)
        dump_doc(path, existing)


def read_box_enable_vault(path: Path, *, default_from: Path | None = None) -> bool:
    """Return the box-scope ``box.enable_vault`` value stored at *path*.

    The single reader for the settable box-scope ``box.enable_vault`` key (P2
    clean break): it sources the flag DIRECTLY from the ``box:`` table of the
    box-tier ``settings.yaml``.  An absent file, an absent ``box:`` table, or an
    absent key all fall through to *default_from* (when given), then to the
    built-in default ``True`` (vault on).

    *default_from* is the WORKSET-tier settings file, consulted ONLY when the key
    is absent from the box tier — the R2 downward-default (``box`` ⊂ ``workset``:
    a ``box.*`` key stored at the workset tier is an overridable default for the
    box).  This key is NOT cascade-resolved — it is read directly, off the launch
    path — so the fallback has to be spelled here rather than falling out of the
    resolver.

    ⚑ Only the STANDALONE resolver passes it, and it is load-bearing there: a
    standalone box's ROOT ``settings.yaml`` WAS its box file before the box tier
    moved to ``box_data/settings.yaml`` (M-8), and is its workset tier after — so
    the fallback is what lets an existing standalone box keep a stored
    ``box.enable_vault: false`` with ZERO migration.  Primary/named pass nothing,
    so their behaviour is byte-identical to before P2.  (Generalizing the fallback
    to every mode would make a ``workset set box.enable_vault=false`` — today a
    silent no-op — go live machine-wide; a real defect, but not this phase's.)

    Box identity derives entirely from the registries (``box_resolve``) — there
    is no on-disk ``project:`` identity section (P8b sparse create) — while
    ``enable_vault`` stays a plain box-settings read: the two concerns are
    decoupled.  Paired writer: :func:`write_box_enable_vault`.
    """
    for candidate in (path, default_from):
        if candidate is None or not candidate.exists():
            continue
        box_tbl = load_doc(candidate).get("box") or {}
        if "enable_vault" in box_tbl:
            return box_tbl["enable_vault"]
    return True


def carried_box_settings(box_tier: Path, workset_tier: Path | None) -> dict:
    """The box-scope settings a LIFECYCLE op carries from a source box.

    ``convert`` / ``move`` / ``duplicate`` all make a NEW box that inherits the
    source's box-scope settings.  Post-P2 those live in the source's BOX TIER, so
    that file's content is carried verbatim (including non-``box:`` sections such
    as agent config).

    **The legacy underlay.** A standalone box created BEFORE the box tier existed
    wrote its ``box.*`` keys into its ROOT file — which is its WORKSET tier now
    (M-8).  Its box tier is therefore absent or partial, so the workset tier's
    ``box:`` subtree is underlaid beneath the box tier's (box tier WINS, per R2).
    Without this, every pre-P2 standalone box silently loses ``box.image`` and
    friends the first time it is converted, moved or duplicated.

    **``workset:`` is never carried.**  Workset-scope keys are the source's OWN
    identity (``workset.kuid``); the destination establishes its own.  ⚑ This is
    HYGIENE, not a hazard fix: a stray ``workset.kuid`` sitting in a BOX TIER is
    INERT, because the kuid is read directly from the ROOT file, never resolved
    through the cascade — pinned by
    ``test_kuid_is_read_from_the_root_file_not_the_box_tier`` and verified
    experimentally.  (An earlier version of this code claimed carrying it would
    OVERRIDE the destination's fresh kuid and used that to justify dropping the
    legacy underlay entirely.  That claim was wrong on both counts, and dropping
    the underlay is what caused the loss described above.)

    Returns the DOC to write at the DESTINATION's box tier; ``{}`` when the source
    carries nothing.
    """
    doc = dict(load_doc(box_tier))
    legacy = (load_doc(workset_tier).get("box") or {}) if workset_tier else {}
    if isinstance(legacy, dict) and legacy:
        box_sec = dict(legacy)
        box_sec.update(doc.get("box") or {})   # box tier wins (R2)
        doc["box"] = box_sec
    doc.pop("workset", None)
    return doc


def read_workset_kuid(path: Path) -> str:
    """Return the stored ``workset.kuid`` value at *path* (default the SENTINEL).

    The reader for the settable ``workset.kuid`` key (settings-conformance P6d):
    it sources the kuid DIRECTLY from the ``workset:`` table of a box's
    ``settings.yaml`` (for a STANDALONE box that single file plays the WORKSET
    tier). An absent file / ``workset:`` table / key yields the reserved
    :data:`kanibako.kuid.SENTINEL` (``"00000"``) — the primary/named default and
    the "no real kuid yet" marker. Mirrors :func:`read_box_enable_vault` (the P2
    reader-default pattern): the DEFAULT lives here, not in a cascade floor.
    """
    from kanibako import kuid

    if not path.exists():
        return kuid.SENTINEL
    data = load_doc(path)
    value = (data.get("workset") or {}).get("kuid", kuid.SENTINEL)
    return str(value)


def read_workset_skip_kuid_check(path: Path) -> bool:
    """Return the stored ``workset.skip_kuid_check`` bool at *path* (default True).

    The reader for the settable ``workset.skip_kuid_check`` key (P6d; spec default
    ``true`` — the advisory "invalid KUID" warning is OPT-IN strictness, INVERTING
    the old D9). Sourced from the ``workset:`` table of a box's ``settings.yaml``.
    An absent file / table / key yields ``True`` (checking OFF). Mirrors
    :func:`read_box_enable_vault` — the DEFAULT lives here, not a cascade floor.
    """
    if not path.exists():
        return True
    data = load_doc(path)
    return bool((data.get("workset") or {}).get("skip_kuid_check", True))


def _split_config_key(flat_key: str) -> tuple[str, str]:
    """Split a flat config key into (section, key).

    ``"box_image"``       → ``("box", "image")``
    ``"paths_dot_path"``  → ``("paths", "dot_path")``
    ``"some_scalar"``     → ``("", "some_scalar")`` (top-level scalar field)

    A flat key with no recognised section prefix is a TOP-LEVEL scalar field;
    it returns an empty section rather than raising
    (the typed writer in ``config_interface`` is the routed set/get/reset path —
    this helper only serves the few remaining flat-key callers and must never
    crash on an advertised key).
    """
    for prefix in ("paths_", "box_"):
        if flat_key.startswith(prefix):
            section = prefix.rstrip("_")
            key = flat_key[len(prefix):]
            return section, key
    return "", flat_key


def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    """Write or update a single key in a settings.yaml.

    *flat_key* is the underscore-joined config name (e.g. ``"box_image"``).
    """
    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    if not section:
        # Top-level scalar field (no recognised section prefix).
        data[key] = value
        dump_doc(path, data)
        return
    sec = data.get(section)
    if not isinstance(sec, dict):
        sec = {}
        data[section] = sec
    sec[key] = value
    dump_doc(path, data)


def unset_project_config_key(path: Path, flat_key: str) -> bool:
    """Remove a single key from a settings.yaml.

    Returns True if the key was found and removed, False if it was not present.
    """
    if not path.exists():
        return False

    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    if not section:
        # Top-level scalar field (no recognised section prefix).
        if key not in data:
            return False
        del data[key]
        dump_doc(path, data)
        return True
    sec = data.get(section)
    if not isinstance(sec, dict) or key not in sec:
        return False
    del sec[key]
    # Clean up an empty section.
    if not sec:
        data.pop(section, None)
    dump_doc(path, data)
    return True


def load_project_overrides(path: Path) -> dict[str, str]:
    """Load only the project-level overrides from a settings.yaml.

    Returns a dict of flat_key → value for keys that differ from defaults.
    """
    if not path.exists():
        return {}
    proj_cfg = load_config(path)
    defaults = KanibakoConfig()
    overrides: dict[str, str] = {}
    for fld in fields(proj_cfg):
        val = getattr(proj_cfg, fld.name)
        if val != getattr(defaults, fld.name):
            overrides[fld.name] = val
    return overrides


# ---------------------------------------------------------------------------
# Target settings overrides (per-project)
# ---------------------------------------------------------------------------

def read_agent_settings(path: Path, agent_name: str) -> dict[str, str]:
    """Read agent-keyed agent-state overrides from a config file's ``agent`` table.

    Override sections are keyed per agent under ``agent.<agent_name>``, layered
    over the reserved any-agent ``agent.default`` tier (the agent-specific value
    wins within a single file). This stops an override set while a box is on one
    agent (e.g. ``model`` under ``agent.claude``) from bleeding onto another
    agent after the box is switched (e.g. to ``goose``); the agent SELECTION is
    not here either — it is the request ``pref.system.agent`` (spec §2h).

    ``agent.default`` is RESERVED as the any-agent default tier; no real agent
    may be named ``default``.

    **No pass-1 migration.** A legacy FLAT ``[agent]`` table (scalar values
    written directly under ``agent``, e.g. ``agent.model``) is treated as UNSET —
    only nested per-agent dicts (``agent.default`` / ``agent.<agent_name>``) are
    honored. Configs are hand-edited to the new shape. The common no-config case
    (absent file, or absent/empty ``agent`` table) still returns ``{}`` unchanged.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return {}
    out: dict[str, str] = {}
    default_sec = agent.get("default")
    if isinstance(default_sec, dict):
        out.update({k: str(v) for k, v in default_sec.items()})
    agent_sec = agent.get(agent_name)
    if isinstance(agent_sec, dict):
        out.update({k: str(v) for k, v in agent_sec.items()})
    return out


def read_system_agent(system_path: Path | None) -> str | None:
    """Read the stored ``system.agent`` SETTING from the system settings tier.

    ``system.agent`` (spec §2g) is the CURRENT agent's name — a system-scope
    SETTINGS key (behavior, not a config path), so it lives in the ``system:``
    table of the system settings file ``@config.settings`` =
    ``@config.data/global/settings.yaml`` (the ``std.settings`` path), exactly
    where ``assemble_levels`` reads the system tier from.  Callers pass that
    settings-file path as *system_path* (NOT ``~/.config/kanibako_config.yaml``,
    which holds only the bootstrap PATH tables).

    ⮕ **RENAMED + RELOCATED (P7, spec §2g).**  Was ``read_default_agent``, reading
    ``system.default_agent`` out of the reserved any-agent ``agent.default`` table
    under the leaf ``default_agent`` — a location that made the stored default an
    UNDECLARED key riding the AGENT tier of the real cascade.  A store still
    carrying the old leaf is migration M-4 (documentation only) and is REFUSED by
    name at assembly (``settings_assemble`` retired-key check).

    ⚑ This is the PRE-CASCADE reader, kept for the two callers that need the
    stored value before a snapshot exists (``start``'s box-independent persona
    pre-flight, and ``setup``'s round-trip).  The LAUNCH does not use it: agent
    selection resolves ``system.agent`` off the snapshot, prefs included
    (:mod:`kanibako.settings.agent_select`).

    Returns the configured agent name, or ``None`` when unset/empty (meaning "no
    system default" — callers fall through to the installed-count rule).
    """
    if system_path is None or not system_path.exists():
        return None
    data = load_doc(system_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("agent") or "").strip()
    return value or None


def read_setup_completed(config_path: Path | None) -> str | None:
    """Read the ``system.setup_completed`` marker from the CONFIG file.

    ``system.setup_completed`` is a host-global ``system.*`` value recording the
    build version at which ``kanibako setup`` last succeeded (W1).  Unlike
    ``system.agent`` it is a plain ``[system]`` leaf in
    ``~/.config/kanibako_config.yaml`` (NOT a settings-tier value), and the typed loader
    (``load_config`` → ``KanibakoConfig``) maps only KNOWN system leaves and
    ignores unknown ones — so this RAW reader is required for the setup-completion
    gate to read it back.  *config_path* is the kanibako_config.yaml CONFIG file.

    Returns the stored version string, or ``None`` when the file/key is absent or
    empty (meaning "setup never run" — the gate then re-nudges).
    """
    if config_path is None or not config_path.exists():
        return None
    data = load_doc(config_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("setup_completed", "")).strip()
    return value or None


def read_templates_stamp(config_path: Path | None) -> str | None:
    """Read the ``system.templates_stamp`` content-manifest hash from the CONFIG file.

    A sibling of :func:`read_setup_completed`: ``system.templates_stamp`` is a
    plain ``[system]`` leaf in ``~/.config/kanibako_config.yaml`` recording the
    packaged-template content digest at which the runtime template dirs were last
    installed/refreshed (first-run init or ``kanibako setup``).  The typed loader
    ignores unknown ``[system]`` leaves, so this RAW reader is required for the
    template-staleness gate to read it back.  *config_path* is the
    kanibako_config.yaml CONFIG file.

    Returns the stored digest string, or ``None`` when the file/key is absent or
    empty (a host that predates the stamp — the gate treats that as STALE).
    """
    if config_path is None or not config_path.exists():
        return None
    data = load_doc(config_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("templates_stamp", "")).strip()
    return value or None


def template_staleness_gate(config_path: Path | None) -> None:
    """HARD template-staleness gate: raise :class:`ConfigError` when stale.

    STALE ⟺ the recorded ``system.templates_stamp`` differs from the CURRENT
    packaged-template digest (:func:`kanibako.launch.templates.packaged_templates_digest`
    over the INSTALLED agent plugins, ``sorted(discover_targets())`` — matching
    first-run ``target_names``).  A host that predates the stamp reads ``None``,
    which is likewise ``!= digest`` → stale.  Returns ``None`` when current.

    An UNINITIALIZED host (no config file yet) is NOT gated: first-run init
    (``cli._ensure_initialized``) installs the templates and writes the stamp, and
    the nudge runs BEFORE init on that very first invocation — hard-blocking it
    would break first run.  Only an already-initialized host (config file present)
    with a missing/stale stamp trips the gate.  The comparison is over the PACKAGED
    src digest + the recorded stamp, so it needs no host-side template dirs.
    """
    from kanibako.errors import ConfigError

    if config_path is None or not config_path.exists():
        return

    from kanibako.targets import discover_targets
    from kanibako.launch.templates import packaged_templates_digest

    agent_names = sorted(discover_targets().keys())
    current = packaged_templates_digest(agent_names)
    stored = read_templates_stamp(config_path)
    if stored != current:
        raise ConfigError(
            "kanibako's bundled templates changed since setup was last run. "
            "Run 'kanibako setup' to update them."
        )


def setup_compat_gate(config_path: Path | None) -> str | None:
    """Run the 5-band setup/config compatibility gate for *config_path*.

    Compares the recorded ``system.setup_completed`` marker (ConfigVer) against
    the running build (CurrentVer = ``__version__``) and the two build constants
    ``SETUP_BCV``/``SETUP_FCV``.  All comparisons are by BASE version (PEP 440
    ``packaging.version.Version`` — the project's own versions, e.g.
    ``1.6.0.dev25`` / ``1.6.0-rc1``, are PEP 440), so a dev/rc build of the same
    base as the released marker reads as ``==``, not "from the future".

    The bands (design ``plans/2026-06-23-setup-version-tiers-NEXT.md``):

    * ``ConfigVer > CurrentVer`` → **raise** :class:`~kanibako.errors.ConfigError`
      (config from a NEWER build than is running).
    * ``ConfigVer == CurrentVer`` → ``None`` (fully current; no message).
    * ``FCV <= ConfigVer < CurrentVer`` → **silently bump** the marker forward to
      CurrentVer ONCE (via ``config_interface.write_system_value``), return
      ``None``.  A failed bump write (e.g. read-only config) is swallowed so the
      gate never blocks a command.
    * ``BCV <= ConfigVer < FCV`` → return the NUDGE string (non-blocking;
      re-run ``kanibako setup``).
    * ``ConfigVer < BCV`` → **raise** :class:`~kanibako.errors.ConfigError`
      (too old to auto-fill; must re-run ``kanibako setup``).
    * absent marker → return the first-run nudge (Jei 2026-06-23).
    * unparseable marker → ``None`` (don't nag a hand-edited value).

    The two ``raise`` bands are the only blocking outcomes; the CLI surfaces them
    as rc1.  Returning a string is a NON-BLOCKING advisory the caller prints to
    stderr before continuing.
    """
    from kanibako import SETUP_BCV, SETUP_FCV, __version__
    from kanibako.errors import ConfigError

    marker = read_setup_completed(config_path)
    if marker is None:
        return "kanibako isn't set up yet. Run 'kanibako setup' to get started."

    from packaging.version import InvalidVersion, Version

    try:
        config_ver = Version(Version(marker).base_version)
    except InvalidVersion:
        # Hand-edited / unrecognized marker: assume the user knows what they're
        # doing; don't nag and don't block.
        return None

    current_ver = Version(Version(__version__).base_version)
    bcv = Version(Version(SETUP_BCV).base_version)
    fcv = Version(Version(SETUP_FCV).base_version)

    if config_ver > current_ver:
        raise ConfigError(
            "This kanibako config was written by a newer kanibako "
            f"({marker}) than the one running ({__version__}). "
            "Upgrade kanibako, or re-run 'kanibako setup' to rebuild it."
        )
    if config_ver == current_ver:
        return None
    if config_ver >= fcv:
        # Forward-compatible (nothing new since): silently advance the marker so
        # subsequent runs hit the ``==`` no-op.  The bump must never block — a
        # failed write (read-only config, missing path) falls through silently.
        try:
            from kanibako.settings.config_interface import write_system_value

            if config_path is not None:
                write_system_value(config_path, "setup_completed", __version__)
        except Exception:  # pragma: no cover - defensive; bump is best-effort
            pass
        return None
    if config_ver >= bcv:
        return "kanibako setup is out of date — re-run 'kanibako setup'."
    raise ConfigError(
        f"This kanibako config ({marker}) is too old to auto-update. "
        "Re-run 'kanibako setup' before agent commands."
    )


# Pseudo-agents are DISCOUNTED from the implicit installed-count rule (so a host
# with one real agent + no_agent is unambiguous, not "2+"), but remain EXPLICITLY
# selectable (``--agent no_agent`` / ``pref.system.agent: no_agent``).
_PSEUDO_AGENTS = frozenset({"no_agent", "general"})


def resolve_agent(
    *,
    explicit_agent: str | None,
    requested: str | None = None,
    project_path: Path | None = None,
) -> str:
    """Validate/arbitrate the effective agent name (+ the installed-count rule).

    ⮕ **P7:** the CASCADE moved out.  ``system.agent`` and the ``pref.system.agent``
    requests of the workset/box files are resolved off the launch snapshot by
    :func:`kanibako.settings.agent_select.select_agent`, which passes the winner here as
    *requested*.  What stays here is what is NOT a key: name VALIDATION against the
    installed set, persona-ref canonicalisation, and the installed-count rule.
    (Was: ``explicit_agent > box_agent_name > workset_agent > system default``,
    with ``box.agent_name`` — RETIRED, spec §2b — as the box tier.)

    Precedence: *explicit_agent* (the §1A CLI level) > *requested* (whatever the
    settings cascade resolved).  The FIRST non-empty one "resolves a name".

    A resolved name is validated against the installed set
    (``discover_targets`` keys — exactly what ``agent list`` uses):

    * installed -> return it;
    * not installed -> raise :class:`~kanibako.errors.AgentNotInstalledError`
      (actionable: names the agent + how to install it).

    Nothing resolved -> the installed-count rule (NO ordering, NO tie-break):

    * exactly 1 installed -> return that name;
    * 0 installed -> raise :class:`~kanibako.errors.NoAgentInstalledError` (Gate-2b);
    * 2+ installed -> raise :class:`~kanibako.errors.NoAgentSelectedError` (Gate-2a).
    """
    # Lazy import: kanibako.targets imports paths/config indirectly, so importing
    # it at module scope risks a cycle. Mirror discover_targets' use elsewhere.
    from kanibako.agent_ref import canonicalize_agent_ref, harness_of
    from kanibako.errors import (
        AgentNotInstalledError,
        NoAgentInstalledError,
        NoAgentSelectedError,
    )
    from kanibako.install_method import install_command
    from kanibako.targets import discover_targets

    def _clean(value: str | None) -> str:
        return (value or "").strip()

    installed = set(discover_targets(project_path).keys())
    # The implicit installed-count rule (1->use / 0->error / 2+->error)
    # considers only REAL launchable agents — pseudo/catch-all targets
    # (``no_agent``, ``general``) are EXCLUDED so a host with exactly one real
    # agent plus the built-in shell fallback is unambiguous (not "2+"), and a
    # host with zero real agents reports Gate-2b (not "use no_agent").  Pseudo
    # agents stay EXPLICITLY selectable via the cascade (handled below against
    # the full `installed` set).
    real_installed = installed - _PSEUDO_AGENTS

    # Cascade: first non-empty tier resolves a name.  Each ref source may be a
    # persona ref (``persona+harness``); canonicalise the winning tier to its
    # node-name (``persona℘harness``; bare stays byte-identical) so callers see a
    # uniform node-name.  The canonicalize call also VALIDATES the ref shape
    # (raises ConfigError on a malformed segment).
    raw_resolved = _clean(explicit_agent) or _clean(requested)

    if raw_resolved:
        # Canonicalise ``+`` -> ``℘`` and validate the ref shape; the HARNESS
        # (right of ``℘``, the whole name when bare) is what must be an installed
        # target — NOT the composite node-name (a persona's name segment is free-form).
        node = canonicalize_agent_ref(raw_resolved)
        harness = harness_of(node)
        # An explicitly-named harness (incl. a pseudo agent like ``no_agent``)
        # validates against the FULL installed set.
        if harness in installed:
            return node
        raise AgentNotInstalledError(
            f"Agent '{harness}' is not installed. Install it with:\n"
            f"  {install_command(f'kanibako-agent-{harness}')}\n"
            f"Or run 'kanibako agent list' to see installed agents."
        )

    # Nothing resolved -> installed-count rule (REAL agents only).
    if len(real_installed) == 1:
        return next(iter(real_installed))
    if len(real_installed) == 0:
        raise NoAgentInstalledError(
            "No agent plugins are installed. Install one, e.g.:\n"
            f"  {install_command('kanibako-agent-claude')}\n"
            "Access via shell: kanibako shell"
        )
    raise NoAgentSelectedError(
        "No agent selected; run 'kanibako setup' to select one or "
        "'kanibako shell' to access the container via command shell."
    )


def write_agent_setting(path: Path, key: str, value: str, agent_name: str) -> None:
    """Write a single agent-state override under ``agent.<agent_name>``.

    Preserves all other sections and other agents' agent subsections. Pass the
    reserved ``"default"`` agent name to target the any-agent default tier.
    """
    existing = load_doc(path)
    agent = existing.get("agent")
    if not isinstance(agent, dict):
        agent = {}
        existing["agent"] = agent
    agent_sec = agent.get(agent_name)
    if not isinstance(agent_sec, dict):
        agent_sec = {}
        agent[agent_name] = agent_sec
    agent_sec[key] = value
    dump_doc(path, existing)


# ---------------------------------------------------------------------------
# Scope categories (settings-framework {scope}.<category>.* — the unified
# masks/bindings/caches/seeded/shared/synced/env primitive)
# ---------------------------------------------------------------------------

def _flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict into DOTTED-key form, stringifying scalar leaves.

    ``{"system": {"bindings": {"rw": {"foo": "h:g"}}}}`` →
    ``{"system.bindings.rw.foo": "h:g"}``.
    """
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dotted(v, key))
        else:
            out[key] = str(v)
    return out
