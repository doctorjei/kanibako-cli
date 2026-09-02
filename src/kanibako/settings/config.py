"""Bootstrap config file: YAML load/write, the flat merged object, pre-cascade readers."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako._atomic import atomic_write_text
from kanibako.errors import ConfigError
from kanibako.settings.bootstrap import (CONFIG_FILE, SITE_CONFIG_DIR, SITE_CONFIG_FILE,
                                         SITE_SETTINGS_FILE)
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.messages import ERR_CONFIG_LAYER1_SETTINGS

if TYPE_CHECKING:
    # ⚑ TYPE-ONLY: ``keystore`` imports this module transitively, so a runtime import
    # here closes the cycle the whole file's lazy-import style exists to avoid.
    from kanibako.settings.keystore import KeyStore


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Per-box construct-time metadata + box-tier settings cascade file (spec §2c meta.box.*)
BOX_META_FILE = "box.yaml"
# Workset-tier settings cascade file (spec §2c)
WORKSET_META_FILE = "workset.yaml"
# Agent-tier settings cascade file, INSIDE the per-agent store dir (spec §2d
# meta.agent.<agent>.settings).  ⚑ The SYSTEM tier is NOT here: it stays
# @config.settings = global/settings.yaml.
AGENT_META_FILE = "agent.yaml"

# Shared truth tables: the typed `config set` writer AND the box.meta writer.
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


def coerce_bool(value: object) -> bool | None:
    """Coerce a config value to a real bool via the shared truth table (None if not a bool literal)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
    return None


_DEFAULTS: dict[str, str] = {
    "box_image": "ghcr.io/doctorjei/kanibako-oci:latest",
    "box_shell": "",
}


@dataclass
class KanibakoConfig:
    """The flat merged SETTINGS object (defaults < workset < box < CLI).

    ⚑ NO ``config_paths`` FIELD, AND NO ``paths_project_toml`` (R153, 2026-08-31).  The
    first was Layer 1 living inside a Layer-2 object, which is what let one read answer both
    layers' questions — it is :class:`BootstrapConfig` now.  The second named
    ``paths.project_toml``, which the keyspace does not declare at all (spec §0), and no
    caller ever read it.
    """

    box_image: str = _DEFAULTS["box_image"]
    # ⚑ NO ``box_agent_name`` field (P7, spec §2b) — the selection is a KEY.
    box_shell: str = _DEFAULTS["box_shell"]
    box_share_images: bool = False
    # ⚑ THE CARRIER OF ``box.enable_vault``'s DECLARED DEFAULT (2026-08-29).  It used to
    # live inside ``read_box_enable_vault``'s ``return True``, which made the reader the
    # only carrier — so the key answered at NO launch terminus and a base- or system-tier
    # value could not reach the vault binds at all.  It is a field here for the same
    # reason ``box_share_images`` is: the field default IS the floor the keyspace
    # resolves from (:func:`box_scalar_defaults_floor`).
    box_enable_vault: bool = True


@dataclass(frozen=True)
class BootstrapConfig:
    """The Layer-1 bootstrap file's WHOLE content: the ``config.*`` foundation, and nothing else.

    ⚑⚑ THE TYPE IS THE RULE (P3/P4; Jei's ruling, 2026-08-31).  ``kanibako.cfg``
    cannot have settings (Jei, 2026-08-26: *"kanibako_config.yaml <-- cannot have settings.
    Period."*), and spec §1 gives Layer 1 the ``config.*`` bootstrap paths alone.  That rule
    used to be a ``config.``-PREFIX FILTER spelled at each of the four Layer-1 read sites,
    over a :class:`KanibakoConfig` that also carried the box scalars — so a Layer-1 read
    still RETURNED settings (``load_config(<file with a box: table>).box_image`` was the
    file's value), and the filter dropped the rest in SILENCE.  This class has nowhere to
    put a settings value, so the filter is not weakened here — it is DELETED, because
    nothing it could have removed can be built.  A settings table in that file now REFUSES,
    naming the file and the keys (:func:`bootstrap_config_paths`).
    """

    config_paths: dict[str, str] = field(default_factory=dict)


#: The Layer-1 file's ONE legal top-level table (spec §1). Everything else in that document
#: is a settings key, which the file cannot carry.
_LAYER1_TABLE = "config"


def config_file_path(config_home: Path) -> Path:
    """The bootstrap config file ``$XDG_CONFIG_HOME/kanibako.cfg`` (JC-1 clean break)."""
    return config_home / CONFIG_FILE


def _layer1_settings_keys(data: dict) -> list[str]:
    """Every SETTINGS entry a Layer-1 document carries, dotted and sorted; empty ⇒ the file is clean.

    ⚑⚑ A TABLE WITH NO LEAF IS NAMED BY ITS TABLE NAME, and that is the whole reason for the
    ``or [name]`` (Jei, 2026-08-31).  The three empty spellings a user reads as identical —
    ``box:`` with nothing under it (which YAML parses to ``None``, NOT ``{}``), an explicit
    ``box: {}``, and a ``box:`` whose only leaf is itself an empty table — used to give TWO
    different answers: the first was refused as a bare ``box`` (the non-dict arm), the other
    two were silently accepted.  Convention 0: two forms meaning one thing are worse than one
    awkward form meaning one thing, and the silent arm was the only thing in this rule that
    behaved like a carve-out.  All three are settings tables that do not belong in this file,
    so all three are refused, and the message names the table it can see.
    """
    keys: list[str] = []
    for name, value in data.items():
        if name == _LAYER1_TABLE:
            continue
        # ⚑ ``_flatten_dotted`` handles a scalar too (``box_image: x`` → ``box_image``); the
        # fallback is for a table that flattens away to nothing, at any depth.
        # ⚑ THE LEFT OPERAND IS A DICT, and ``extend`` takes its KEYS — so the ``or`` tests
        # the MAPPING's emptiness, never a leaf's truthiness.  A falsy leaf (``foo: 0``,
        # ``foo: ''``) yields a one-entry dict and is named like any other.
        keys.extend(_flatten_dotted({name: value}) or [name])
    return sorted(keys)


def bootstrap_config_paths(path: Path) -> dict[str, str]:
    """The Layer-1 file's ``config.*`` foundation, read from its ``config:`` table ALONE.

    ⚑ NO FILTER, AND THAT IS THE POINT (P4).  The walk STARTS at the ``config:`` table, so
    a ``config.`` prefix is the only thing it can produce; the rule is in the shape of the
    read rather than in a test applied after it.
    🛑 A settings table here is REFUSED, not dropped (Jei, 2026-08-31) — a user running a
    different image than their file says should learn it.
    """
    data = load_doc(path)
    settings_keys = _layer1_settings_keys(data)
    if settings_keys:
        raise ConfigError(ERR_CONFIG_LAYER1_SETTINGS % (path, "\n  ".join(settings_keys)))
    table = data.get(_LAYER1_TABLE)
    return _flatten_dotted(table, _LAYER1_TABLE) if isinstance(table, dict) else {}


def system_path_set_values(settings_path: Path) -> dict[str, str]:
    """A SETTINGS file's ``system.*`` set-values, dotted — the Layer-2 half of the path tier.

    ⚑ ITS OWN READER since 2026-08-31.  This was ``load_config(path).config_paths`` — the
    very call the LAYER-1 read used, over one field that held ``config.*`` and ``system.*``
    together.  One function answering two layers' questions is what let each layer's file
    speak for the other; the walk here starts at the ``system:`` table, so ``system.`` is
    the only prefix it can produce.
    ⚑ NOT filtered to the path tier — that is :func:`~kanibako.settings.paths.load_system_config`'s
    own P13 job, and this file's ``system:`` table legitimately holds ``system.agent`` and
    the category families too.
    """
    table = load_doc(settings_path).get("system")
    return _flatten_dotted(table, "system") if isinstance(table, dict) else {}


def config_base_path() -> Path:
    """The machine-wide CONFIG base file — the bootstrap-PATH set's least-specific layer."""
    return Path(SITE_CONFIG_DIR) / SITE_CONFIG_FILE


def settings_base_path() -> Path:
    """The machine-wide SETTINGS base file — the behavior cascade's bottom layer, below every scope."""
    return Path(SITE_CONFIG_DIR) / SITE_SETTINGS_FILE


#: The box-scope SCALAR keys resolved through the KEYSPACE (B6, R-11a(a)):
#: dotted key → the flat ``KanibakoConfig`` field it lands on.
#: ⚑⚑ IT IS ALSO THE READ's KEY SET since 2026-08-31 — :func:`_present_scalar_fields` walks
#: a settings document THROUGH these dotted spellings, so this table is the one place that
#: says which scalars exist and how they are spelled, for the read and the resolve alike.
#: ⚑ ``box.enable_vault`` JOINED 2026-08-29 as the fourth.  It was the last member of the
#: "pre-cascade reader owns the default" pattern, and its two halves were both defects: the
#: declared default reached no launch snapshot, and :func:`read_box_enable_vault` opened
#: exactly TWO files (box tier + workset tier), so a value set at the BASE or SYSTEM tier
#: was accepted, persisted, echoed back by ``system get`` — and then ignored by every box.
#: The pattern's rationale ("a caller runs before a snapshot exists") is true of a FULL
#: snapshot and does not hold here: this resolve needs only FILE PATHS, which the callers
#: compute two lines above the read.  🛑 The AUTHORED-value read stays a direct box-tier
#: open — see :func:`carried_box_settings` for why the cascade cannot answer that question.
_BOX_SCALAR_FIELDS: dict[str, str] = {
    "box.image": "box_image",
    "box.share_images": "box_share_images",
    "box.shell": "box_shell",
    "box.enable_vault": "box_enable_vault",
}


def _scalar_value(value: object) -> object:
    """A settings-file scalar as the flat object carries it (``None`` = the reset sentinel)."""
    if isinstance(value, bool) or value is None:
        return value
    return str(value)


def _present_scalar_fields(path: Path) -> dict[str, object]:
    """The DECLARED box scalars PRESENT in a SETTINGS file (``None`` = the reset sentinel).

    ⚑⚑ KEYED ON THE DECLARED DOTTED KEYS (:data:`_BOX_SCALAR_FIELDS`), NEVER ON A FLATTENED
    NAMESPACE.  This used to flatten the whole document to underscore-joined names and keep
    whichever matched a :class:`KanibakoConfig` FIELD name — a namespace that COLLIDES with
    those field names, so an undeclared top-level ``box_image:`` resolved identically to the
    declared ``box: image:`` and spec §0's closed keyspace was breached by the shape of the
    read.  Walking IN through the declared spelling makes the flat one unreachable rather
    than refused by a list (P4), and it is also why the ``config``/``system`` pops are gone:
    a table the walk never enters cannot leak.
    """
    data = load_doc(path)
    present: dict[str, object] = {}
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        section, leaf = dotted.split(".", 1)
        table = data.get(section)
        if isinstance(table, dict) and leaf in table:
            present[field_name] = _scalar_value(table[leaf])
    return present


def load_config(path: Path) -> BootstrapConfig:
    """Read the LAYER-1 bootstrap file — the one reader of ``kanibako.cfg``.

    ⚑⚑ IT RETURNS A :class:`BootstrapConfig`, AND THAT IS THE WHOLE OF THE 2026-08-31
    RULING: a Layer-1 read has no settings field to return.  It was a GENERAL document
    reader — the same call read the settings file — which is how the Layer-1 file came to
    hand back a ``box.image`` it may not carry.  The box scalars are read from SETTINGS
    files by :func:`load_merged_config`; a settings file's ``system.*`` path set-values by
    :func:`system_path_set_values`.
    """
    return BootstrapConfig(config_paths=bootstrap_config_paths(path))


def box_scalar_defaults_floor() -> dict[str, object]:
    """The box scalars' DECLARED-DEFAULT floor — the ONE recipe every floor builder uses.

    ⚑⚑ DECLARED DEFAULTS, NEVER FILE VALUES.  ``kanibako.cfg`` cannot have
    settings (Jei, 2026-08-26: *"kanibako_config.yaml <-- cannot have settings.
    Period."*), so a floor built by reading that file is the violation; a floor built
    from the declared defaults is what spec §1/§2b sanction.  Those two were ONE
    expression until now — ``getattr(load_config(cf), field)`` is the file's value when
    the file speaks and the default when it does not — so they are SEPARATED here rather
    than deleted.  🛑 Do not delete the floor itself: ``@box.image`` resolves through it,
    and without it a stored ``@box.image`` dangles at launch AND at set time.

    Two consumers, deliberately one recipe: :func:`_resolve_box_scalars` (the launch-side
    merged resolve) and ``config_interface._category_set_lookups`` (the set-time E3 probe).
    """
    defaults = KanibakoConfig()
    floor: dict[str, object] = {}
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        value = getattr(defaults, field_name)
        # ⚑ ``build_launch_snapshot``'s OWN rule, applied here so the two floors agree:
        # a ``""`` is a SUPPRESSION — "absent ≡ no default" (verified in that function:
        # ``if val == "": continue``).  It is what keeps ``box.shell`` out (spec §2b:
        # ``box.shell | <None>``), so a genuinely unset ``@box.shell`` still refuses BY
        # NAME rather than resolving to blank.  ⚑ ``False`` is a VALUE and survives —
        # ``False == ""`` is False.
        if value == "":
            continue
        floor[dotted] = value
    return floor


def _resolve_box_scalars(
    global_path: Path,
    *,
    workset_path: Path | None,
    box_path: Path | None,
    cli_overrides: "dict[str, object] | None",
) -> dict[str, object]:
    """Resolve the box scalars through the KEYSPACE — the ONE resolve behind ``load_merged_config``.

    ⚑ Lazy imports throughout: ``paths`` and ``settings_assemble`` both import
    this module at module load, so hoisting any of these closes the cycle.
    """
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.paths import load_system_config, host_xdg_map, xdg
    from kanibako.settings.settings_cli_level import build_cli_level
    from kanibako.settings.settings_launch import build_launch_snapshot
    from kanibako.settings.settings_resolve import ResolveCtx

    # Path resolution only, deliberately NOT load_std_paths (which materializes
    # the store). ⚑ Not mkdir-free: an unset XDG_RUNTIME_DIR makes one dir here.
    system_path = load_system_config(
        global_path, data_home=xdg("XDG_DATA_HOME", ".local/share"),
        home=Path.home(),
    )["config.settings"]

    # ⚑ THE DECLARED-DEFAULT FLOOR, not the Layer-1 file's ``box:`` table.  That table
    # WAS this floor ("risk 1": values the settings cascade does not read would be
    # STRANDED) until 2026-08-26, when Jei ruled the file cannot carry settings at all.
    # With nothing settings-shaped stored there, there is nothing to strand.
    floor = box_scalar_defaults_floor()

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
        # ⚑ NO PERSONA TIER, deliberately — this resolve is AGENT-LESS.
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
    """Load global config, overlay workset then project then CLI, then run the B6 box-scalar resolve."""
    defaults = KanibakoConfig()

    def _overlay_scalars(cfg: KanibakoConfig, path: Path) -> None:
        """Overlay one file layer's PRESENT scalar/bool fields onto *cfg*."""
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(defaults, k))
            else:
                setattr(cfg, k, v)

    # ⚑⚑ THE LAYER-1 FILE IS NOT A SETTINGS SOURCE (Jei, 2026-08-26: "kanibako_config.yaml
    # <-- cannot have settings. Period.").  It WAS the least-specific FILE source here, and
    # its ``box:`` table overrode the declared defaults; now the scalars START at those
    # defaults and the first thing that can move them is the WORKSET tier.
    # ⚑ *global_path* is still a PARAMETER, and it is not dead: it is what
    # :func:`_resolve_box_scalars` locates the SYSTEM tier from, below.  What is gone is the
    # ``config_paths`` field this function used to fill from it — a settings object never
    # carried Layer 1 legitimately (:class:`BootstrapConfig`, 2026-08-31).
    cfg = KanibakoConfig()
    if workset_path and workset_path.exists():
        _overlay_scalars(cfg, workset_path)
    if project_path and project_path.exists():
        _overlay_scalars(cfg, project_path)
    if cli_overrides:
        valid_keys = {fld.name for fld in fields(cfg)}
        for k, v in cli_overrides.items():
            if k in valid_keys:
                setattr(cfg, k, v)

    # KEYSPACE resolve (B6): a resolved value wins; ABSENT keeps the flat value.
    resolved = _resolve_box_scalars(
        global_path,
        workset_path=workset_path, box_path=project_path,
        cli_overrides=cli_overrides,
    )
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        if dotted not in resolved:
            continue
        setattr(cfg, field_name, _typed_box_scalar(defaults, field_name, resolved[dotted]))
    return cfg


def _typed_box_scalar(defaults: KanibakoConfig, field_name: str, value: object) -> object:
    """Land a resolved box scalar on its field's own type — bool through the truth table.

    ⚑ The BOOL arm is selected off the DATACLASS DEFAULT, not a hand-kept name list, so a
    fourth scalar cannot be added without its coercion (``box.enable_vault``, 2026-08-29:
    a settings file stores ``false``, and ``str(False)`` is the truthy ``"False"``).
    """
    if isinstance(getattr(defaults, field_name), bool):
        coerced = coerce_bool(value)
        return coerced if coerced is not None else bool(value)
    return str(value)


def _system_settings_path(global_path: Path) -> Path | None:
    """``@config.settings`` off the Layer-1 file — the SYSTEM tier, or ``None`` if absent."""
    from kanibako.settings.paths import load_system_config, xdg

    path = load_system_config(
        global_path, data_home=xdg("XDG_DATA_HOME", ".local/share"), home=Path.home(),
    )["config.settings"]
    return path if path.exists() else None


def resolve_box_enable_vault(global_path: Path, *, box_path: Path,
                             workset_path: Path | None) -> bool:
    """``box.enable_vault`` through the FULL cascade — base < system < workset < box.

    ⚑ THE TIER FIX (2026-08-29).  :func:`read_box_enable_vault` opens two files and only
    two, so the BASE floor and the SYSTEM tier were dropped in silence: a
    ``kanibako system set box.enable_vault=false`` returned 0, persisted, was echoed back
    by ``system get``, and every box still came up with the vault created and mounted.
    Here they are cascade LEVELS.  ⚑ Called from the three ``paths.py`` resolvers rather
    than off ``load_merged_config``, because those run BEFORE it and are what fill
    ``ProjectPaths.enable_vault``, which ``core_defaults`` reads to decide whether the vault
    bind rows exist at all — reading the finished snapshot to decide what goes into it is
    circular.  A narrow resolve needs only FILE PATHS, which ``_box_settings_files`` hands
    over two lines above each call.

    ⚑⚑ IT IS A NARROW RESOLVE (:func:`_narrow_box_scalar_cascade`), NOT
    ``_resolve_box_scalars``, AND THE DIFFERENCE IS DELIBERATE — see that function.

    🛑 NOT for the AUTHORED value — that is :func:`read_box_enable_vault` on the box tier
    alone, and the cascade structurally cannot answer it (a merge does not record which
    tier carried a leaf).
    """
    from kanibako.settings.kb_store import __MISSING__
    from kanibako.settings.settings_launch import snapshot_leaf

    snapshot = _narrow_box_scalar_cascade(
        global_path, workset_path=workset_path, box_path=box_path,
    )
    defaults = KanibakoConfig()
    value = snapshot_leaf(snapshot, "box.enable_vault")
    if value is __MISSING__ or value is None:
        return defaults.box_enable_vault
    return bool(_typed_box_scalar(defaults, "box_enable_vault", value))


def _narrow_box_scalar_cascade(
    global_path: Path, *, workset_path: Path | None, box_path: Path | None,
) -> "KeyStore":
    """The box scalars' cascade WITHOUT the launch snapshot's whole-tree §0 audit.

    ⚑⚑ WHY THIS IS NOT ``_resolve_box_scalars``, WHICH RESOLVES THE SAME KEYS OFF THE SAME
    FILES.  That function ends in ``build_launch_snapshot``, whose LAST step is
    ``_refuse_undeclared_snapshot`` — a whole-tree audit that RAISES when any settings file
    in the cascade carries an entry the keyspace does not declare.  That refusal is right
    for a LAUNCH and for ``box show --effective`` (its own message says so, by name).  It
    is wrong HERE, because this resolve runs inside ``paths.resolve_project`` — the PATH
    resolver every verb goes through, including plain ``kanibako box show``, which is the
    ONE surface designed to still work on a box whose file carries an undeclared entry so
    it can print the offending line.  Routing path resolution through the launch audit
    turned that diagnostic into a refusal.

    ⚑ THE SHAPE IS ``settings_launch.resolve_selected_agent``'s — the module's own named
    "narrow resolve that precedes the launch snapshot" — with the declared-default floor
    under the base file.  Nothing here is a second opinion about the cascade:
    ``assemble_levels``, ``merge`` and :func:`box_scalar_defaults_floor` are the same
    single carriers ``_resolve_box_scalars`` uses, and
    ``test_the_narrow_cascade_agrees_with_the_merged_loader`` pins the two answers equal so
    they cannot drift apart.

    ⚑ NO PREF RUNGS, and that is MEASURED, not an omission: ``settings_prefs.ALLOWLIST`` is
    ``("system.agent", "agent.*.**")``, so no §2h request can name a ``box.*`` key at all.
    Splicing the overlays in would move no answer and would import ``apply_prefs``' raise —
    a resolve that refuses an unrelated bad pref, from inside PATH resolution.

    ⚑ NO ``expand``: ``box.enable_vault`` is ``type: bool`` in the manifest, so it cannot
    carry an ``@``-ref, and a whole-tree expansion here would import exactly the failure
    ``resolve_selected_agent`` had to go LENIENT to avoid — an unrelated defective leaf
    aborting a resolve that never needed it.
    """
    from kanibako.settings.settings_assemble import assemble_levels
    from kanibako.settings.settings_merge import merge

    base_levels = assemble_levels(
        agent_name="",
        system_path=_system_settings_path(global_path),
        agent_path=None,
        workset_path=workset_path,
        box_path=box_path,
        floor=box_scalar_defaults_floor(),
    )
    # ``assemble_levels`` returns MOST-SPECIFIC-FIRST: [box, workset, agent.<a>,
    # agent.default, system, base].  The two agent rungs are dropped, not skipped by
    # accident: this resolve has no active agent, exactly as ``_resolve_box_scalars``
    # passes ``agent_path=None``.
    return merge([base_levels[0], base_levels[1], base_levels[4], base_levels[5]])


def write_global_config(path: Path) -> None:
    """Create the bootstrap config file EMPTY — it may carry ``config.*`` and nothing else."""
    # ⚑⚑ THE FILE CANNOT HAVE SETTINGS (Jei, 2026-08-26: "kanibako_config.yaml <-- cannot
    # have settings. Period.").  It used to be created carrying THREE tables:
    #
    #   ``config:``  — a VERBATIM copy of ``bootstrap.CONFIG_PATH_DEFAULTS``
    #   ``system:``  — a verbatim copy of six of the eleven ``SYSTEM_PATH_DEFAULTS`` rows
    #   ``box:``     — the box scalars at their own ``KanibakoConfig`` field defaults
    #
    # The first was Layer-1's own content written at its own default — a fourth carrier of
    # a value ``paths.resolve_config_paths`` already holds as the ``LevelView`` defaults it
    # layers stored values over, so writing it moved nothing and made every default edit
    # need a matching edit here.  The other two were SETTINGS (spec §2g / §2b) in the
    # Layer-1 file, which is the thing the ruling forbids outright.
    #
    # ⚑ THERE IS NO ``cfg`` PARAMETER ANY MORE, and that is the ruling in the signature: a
    # ``KanibakoConfig`` is settings, so there is nothing it could legitimately contribute
    # here.  Keeping it and ignoring it would be a silent no-op for every caller that
    # passed one.  A non-default ``box.image`` belongs in a SETTINGS file — which is where
    # ``kanibako system set box.image=…`` has always written it.
    #
    # ⚑ THE FILE IS STILL CREATED, EMPTY.  ``cli._ensure_initialized`` uses its EXISTENCE
    # as the "already initialized" test, so an absent file re-runs first-run init —
    # packaged-template install and all — on every command forever.
    #
    # ⚑ ZERO BYTES, not ``{}``: this file is the hand-edit surface the ``config.*`` refusal
    # sends users to (``config_keys._config_key_refusal``), and a leading ``{}`` makes an
    # appended ``config:`` block a YAML error.  Written through the SAME atomic writer
    # ``dump_doc`` delegates to, so the create is atomic either way.
    atomic_write_text(path, "")


def write_project_config(path: Path, image: str) -> None:
    """Write or update a box.yaml with the given image."""
    write_project_config_key(path, "box_image", image)


def persist_creation_flags(
    box_settings_path: Path,
    *,
    materializing: bool,
    image: str | None = None,
    share_images: bool | None = None,
) -> None:
    """The §1A **CREATE EXCEPTION** — the ONE gate through which a shadowing CLI flag ever PERSISTS."""
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
    """Sparsely persist the box-scope ``box.enable_vault`` key at *path* (reader: :func:`read_box_enable_vault`)."""
    existing = load_doc(path)
    ev = coerce_bool(enable_vault)
    if ev is False:
        existing.setdefault("box", {})["enable_vault"] = False
        dump_doc(path, existing)
        return
    # Default (True): rewrite ONLY to drop a stale override; no empty file.
    box_sec = existing.get("box")
    if isinstance(box_sec, dict) and "enable_vault" in box_sec:
        box_sec.pop("enable_vault", None)
        dump_doc(path, existing)


def read_box_enable_vault(path: Path) -> bool:
    """What the BOX ITSELF authored for ``box.enable_vault`` at *path* — one file, no cascade.

    ⚑⚑ THIS IS THE **AUTHORED** READER, AND ONLY THAT (2026-08-29).  The RESOLVED value is
    :func:`resolve_box_enable_vault`, which runs the real cascade and therefore honors the
    BASE and SYSTEM tiers this function cannot see.  What survives here is the question a
    MERGE STRUCTURALLY CANNOT ANSWER — *which tier carried it* — and that is what all three
    remaining callers want (``commands/box/_lifecycle.py`` ×2, ``commands/box/_duplicate.py``
    ×1, each feeding a lifecycle op's destination write beside
    :func:`carried_box_settings`).
    🛑 Do NOT give this a workset-tier fallback again, and do NOT route it through the
    cascade: either one pins an INHERITED workset default as a box-scope override at the
    destination, which is exactly the corruption :func:`carried_box_settings` exists to
    prevent.  ⚑ It HAD a *default_from* parameter until 2026-08-29 — the R2 downward
    default (spec §0 "Directional view/set across CONTAINMENT levels") that made
    ``workset create --no-vault`` reach contained boxes.  That capability did not go: it
    MOVED to :func:`resolve_box_enable_vault`, where the workset tier is one cascade level
    among four rather than a second hand-opened file.  The parameter went with it because
    the only remaining thing it could do here is the corruption above.
    """
    if not path.exists():
        return True
    box_tbl = load_doc(path).get("box") or {}
    if "enable_vault" in box_tbl:
        # ⚑ COERCED IN PLACE, through the SAME :func:`_typed_box_scalar` the resolved
        # reader uses (2026-08-29).  A settings file is hand-editable, so the stored leaf
        # can be the STRING ``"false"`` — truthy — and returning it raw made the AUTHORED
        # answer contradict :func:`resolve_box_enable_vault`'s for the one command that
        # ran before the next write normalized the file.  The coercion goes HERE and not
        # through the cascade: the docstring's two prohibitions above still hold.
        return bool(_typed_box_scalar(KanibakoConfig(), "box_enable_vault",
                                      box_tbl["enable_vault"]))
    return True


def carried_box_settings(box_tier: Path) -> dict:
    """The box-scope settings doc a LIFECYCLE op carries to a new box's box tier.

    ⚑ THE BOX TIER AND NOTHING ELSE (Jei, 2026-08-26: "copy/persist only those
    elements that are within the box settings").  A ``box.*`` key at the WORKSET
    tier is an OVERRIDABLE DEFAULT for the boxes that workset contains
    (:func:`read_box_enable_vault`), so persisting it here would PIN it — silently
    converting a workset default into a box-scope override that later workset edits
    cannot reach.  It stays where it is and keeps resolving for the boxes that stay;
    a box that leaves the workset loses it, because the value was the workset's.
    ⚑ *box_tier* is a ``box.yaml``, so a ``workset:`` section in it is a scope
    violation — dropped rather than carried into the destination's identity.
    """
    doc = dict(load_doc(box_tier))
    doc.pop("workset", None)
    return doc


def read_workset_kuid(path: Path) -> str:
    """The stored ``workset.kuid`` at *path*, defaulting to :data:`kanibako.kuid.SENTINEL`."""
    from kanibako import kuid

    if not path.exists():
        return kuid.SENTINEL
    data = load_doc(path)
    value = (data.get("workset") or {}).get("kuid", kuid.SENTINEL)
    return str(value)


def read_workset_skip_kuid_check(path: Path) -> bool:
    """The stored ``workset.skip_kuid_check`` bool at *path*, defaulting to ``True`` (checking OFF)."""
    if not path.exists():
        return True
    data = load_doc(path)
    return bool((data.get("workset") or {}).get("skip_kuid_check", True))


def _split_config_key(flat_key: str) -> tuple[str, str]:
    """Split a flat config key into ``(section, key)``; no recognised prefix → an EMPTY section."""
    for prefix in ("paths_", "box_"):
        if flat_key.startswith(prefix):
            section = prefix.rstrip("_")
            key = flat_key[len(prefix):]
            return section, key
    return "", flat_key


def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    """Write or update a single key in a box.yaml (*flat_key* is underscore-joined)."""
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
    """Remove a single key from a box.yaml; True iff it was found and removed."""
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


def load_project_overrides(path: Path) -> dict[str, object]:
    """The project-level overrides in a box.yaml — flat_key → value for keys differing from defaults.

    ⚑ OFF THE PRESENT SET, not off a loaded object (2026-08-31): ``load_config`` reads the
    LAYER-1 file now, and a box.yaml is a settings file.  The answer is unchanged — a key
    absent from the file, and a present ``None`` (the reset sentinel), both resolve to the
    default and so are not overrides.
    ⚑ The value type is ``object`` because ``box.share_images`` and ``box.enable_vault`` are
    real bools, which is what the callers print.
    """
    defaults = KanibakoConfig()
    return {
        key: value
        for key, value in _present_scalar_fields(path).items()
        if value is not None and value != getattr(defaults, key)
    }


# ---------------------------------------------------------------------------
# Agent settings, agent selection, and the setup-version gate
# ---------------------------------------------------------------------------

def read_agent_settings(path: Path, agent_name: str) -> dict[str, str]:
    """Agent-state overrides from a config file's ``agent`` table: ``agent.default`` under ``agent.<name>``.

    ⚑ A legacy FLAT ``[agent]`` table is treated as UNSET — only nested
    per-agent dicts are honored, and that is deliberate (no pass-1 migration).
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
    """The stored ``system.agent`` SETTING from the system settings tier; ``None`` when unset.

    ⚑ *system_path* is the SETTINGS file (``@config.settings``), NOT
    ``kanibako.cfg``. ⚑ PRE-CASCADE reader — the LAUNCH does not use it.
    """
    if system_path is None or not system_path.exists():
        return None
    data = load_doc(system_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("agent") or "").strip()
    return value or None


def read_setup_completed(settings_path: Path | None) -> str | None:
    """The ``system.setup_completed`` marker from the SYSTEM SETTINGS file; ``None`` means "setup never run".

    ⚑⚑ *settings_path* is ``@config.settings`` = ``<data>/global/settings.yaml``, NOT
    ``kanibako.cfg`` — the SAME file :func:`read_system_agent` reads and the
    launch cascade's system tier assembles from.  It moved there on 2026-08-26 (Jei:
    "there is no reason whatsoever that ``system.setup_completed`` should go in the
    config. It should not. It should go in the global settings file"), which is also
    what spec §2g has always declared: the marker is a Layer-2 ``system.*`` SETTINGS
    key, and Layer-1 holds the ``config.*`` bootstrap paths ALONE (spec §1).
    ⚑ ONE LOCATION, no fallback read: a FRESH install has no settings file at all and
    must read as "setup never run", which is the absent band's NON-BLOCKING nudge
    (:func:`setup_compat_gate`) — never "already set up", and never a block.

    ⚑ A RAW reader is still required — the pre-cascade gate runs before any snapshot.
    """
    if settings_path is None or not settings_path.exists():
        return None
    data = load_doc(settings_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("setup_completed", "")).strip()
    return value or None


# ⚑ ``read_templates_stamp`` + ``template_staleness_gate`` lived here and are
# RETIRED (R-38, M-23); the protection folds into ``setup_compat_gate`` below.


def setup_compat_gate(settings_path: Path | None) -> str | None:
    """Run the 5-band setup/config compatibility gate; a returned string is a NON-BLOCKING advisory.

    ⚑ Every comparison is by BASE version, so a dev/rc build of the same base
    as the released marker reads as ``==``, not "from the future".
    ⚑ *settings_path* is the SYSTEM SETTINGS file, the marker's home since 2026-08-26
    (:func:`read_setup_completed`) — this gate knows exactly one file, as it always did.
    """
    from kanibako import SETUP_BCV, SETUP_FCV, __version__
    from kanibako.errors import ConfigError

    marker = read_setup_completed(settings_path)
    if marker is None:
        return "kanibako isn't set up yet. Run 'kanibako setup' to get started."

    from packaging.version import InvalidVersion, Version

    try:
        config_ver = Version(Version(marker).base_version)
    except InvalidVersion:
        # Hand-edited marker: don't nag and don't block.
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
        # Forward-compatible: advance the marker so later runs hit the ``==``
        # no-op. ⚑ The bump must NEVER block, so a failed write is swallowed.
        try:
            from kanibako.settings.config_interface import write_system_value

            if settings_path is not None:
                write_system_value(settings_path, "setup_completed", __version__)
        except Exception:  # pragma: no cover - defensive; bump is best-effort
            pass
        return None
    if config_ver >= bcv:
        return "kanibako setup is out of date — re-run 'kanibako setup'."
    raise ConfigError(
        f"This kanibako config ({marker}) is too old to auto-update. "
        "Re-run 'kanibako setup' before agent commands."
    )


# Pseudo-agents are DISCOUNTED from the implicit installed-count rule; ``no_agent``
# stays explicitly selectable. ⚑ ``general`` is a SLOT name, not a shipped target.
_PSEUDO_AGENTS = frozenset({"no_agent", "general"})


def resolve_agent(
    *,
    explicit_agent: str | None,
    requested: str | None = None,
    project_path: Path | None = None,
) -> str:
    """Validate/arbitrate the effective agent name against the installed set, plus the count rule.

    ⮕ **P7: the CASCADE moved out** — what stays here is what is NOT a key.
    """
    # ⚑ Lazy: kanibako.targets imports paths/config indirectly (cycle risk).
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
    # The count rule considers only REAL launchable agents; an explicitly-named
    # harness still validates against the FULL `installed` set below.
    real_installed = installed - _PSEUDO_AGENTS

    # First non-empty tier resolves a name.
    raw_resolved = _clean(explicit_agent) or _clean(requested)

    if raw_resolved:
        # ⚑ Canonicalise + validate the ref shape; the HARNESS is what must be
        # installed — NOT the composite node-name (a persona segment is free-form).
        node = canonicalize_agent_ref(raw_resolved)
        harness = harness_of(node)
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
    """Write a single agent-state override under ``agent.<agent_name>``, preserving every other section."""
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


def _flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into DOTTED-key form, stringifying scalar leaves.

    ⚑ NOT a scope-category helper — its callers are the Layer-1 ``config:`` read, the
    Layer-2 ``system:`` path-tier read, and the Layer-1 refusal that names its keys.
    """
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dotted(v, key))
        else:
            out[key] = str(v)
    return out
