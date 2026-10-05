"""Bootstrap config file: YAML load/write, the flat merged object, pre-cascade readers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TypedDict
from kanibako._atomic import atomic_write_text
from kanibako.errors import ConfigError
from kanibako.settings.bootstrap import (BOXES_PATH, CONFIG_FILE, CONFIG_PATH_DEFAULTS,
                                         SITE_CONFIG_DIR, SITE_CONFIG_FILE,
                                         SITE_SETTINGS_FILE, SYSTEM_PATH_DEFAULTS)
from kanibako.settings.config_io import dump_doc, load_doc, refuse_scalar_sections
from kanibako.settings.messages import (ERR_CONFIG_LAYER1_SETTINGS, ERR_CONFIG_LAYER1_TABLE,
                                        ERR_CONFIG_LAYER1_UNDECLARED,
                                        ERR_CONFIG_NULL_PATH_CURE,
                                        ERR_CONFIG_NULL_PATH_HEAD,
                                        ERR_CONFIG_PATH_REF_SCOPE, ERR_CONFIG_REF_ORDER)

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


class _BoxScalarDefaults(TypedDict):
    box_image: str
    box_shell: str | None


_DEFAULTS: _BoxScalarDefaults = {
    "box_image": "ghcr.io/doctorjei/kanibako-oci:latest",
    "box_shell": None,
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
    # ⚑ ``<None>`` IS the declared default, NOT the ``""`` spelling (spec §2b
    # ``box.shell | <None>``, auto-detect): a scalar leaf that admits ``<None>`` holds it,
    # and a consumer reads ``None`` (spec §2h) — never the string ``"None"``, and never a
    # ``""`` a user cannot tell from a value.
    box_shell: str | None = _DEFAULTS["box_shell"]
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

    ⚑ ``config_paths`` is a read-only COPY: ``__post_init__`` copies what it was handed and
    wraps it in a :class:`~types.MappingProxyType`, so ``frozen`` is finally true.
    """

    config_paths: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ⚑ THE COPY, THEN THE PROXY (P3/P8). ``frozen`` alone blocked only the REBIND: the
        # dict stayed live and the constructor kept the caller's own dict, so a frozen
        # instance was mutated from outside (P8, "copy OUT at the boundary"). ``dict(...)``
        # breaks the alias; the proxy then refuses item assignment. ``object.__setattr__``
        # is the documented way to write a field of a frozen dataclass.
        object.__setattr__(self, "config_paths", MappingProxyType(dict(self.config_paths)))


#: The Layer-1 file's ONE legal top-level table (spec §1). Everything else in that document
#: is a settings key, which the file cannot carry.
_LAYER1_TABLE = "config"


def config_file_path(config_home: Path) -> Path:
    """The bootstrap config file ``$XDG_CONFIG_HOME/kanibako.cfg`` (JC-1 clean break)."""
    return config_home / CONFIG_FILE


def user_config_file() -> Path:
    """The value of ``meta.runtime.user.config`` — callers READ it; they do not compose it ([R154])."""
    from kanibako.settings.paths import user_config_home

    return config_file_path(user_config_home())


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

    ⚑ ``str(name)`` BECAUSE A YAML KEY NEED NOT BE A STRING (2026-09-09).  ``1: x``,
    ``true: x`` and ``~: x`` are all legal YAML, and the raw key reached ``sorted`` and
    ``"\\n  ".join`` as an ``int``/``bool``/``None`` — a ``TypeError`` traceback in the one
    file whose whole purpose is that a hand-editing user FINDS OUT.
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
        keys.extend(_flatten_dotted({name: value}) or [str(name)])
    return sorted(keys)


def bootstrap_config_paths(path: Path) -> dict[str, str]:
    """The Layer-1 file's ``config.*`` foundation, read from its ``config:`` table ALONE.

    ⚑ NO FILTER, AND THAT IS THE POINT (P4).  The walk STARTS at the ``config:`` table, so
    a ``config.`` prefix is the only thing it can produce; the rule is in the shape of the
    read rather than in a test applied after it.
    🛑 A settings table here is REFUSED, not dropped (Jei, 2026-08-31) — a user running a
    different image than their file says should learn it.

    ⚑⚑ AND SO IS AN UNDECLARED LEAF *INSIDE* ``config:`` (2026-09-09).  Starting the walk
    at that table is what makes the ``config.`` PREFIX unfakeable; it says nothing about the
    TAIL, so ``config: {nonsense: /x}`` and ``config: {box: {image: X}}`` were carried to
    :func:`~kanibako.settings.paths.resolve_config_paths`.  🛑 THEY WERE NOT MERELY DROPPED
    THERE: that function's OUTPUT loop iterates the DECLARED table, but its inner ``lookup``
    resolves ``@``-refs against a ``LevelView`` built from the FILE's set-values — so an
    undeclared leaf was an undeclared NAME THE RESOLVER WOULD FOLLOW, and
    ``data: "@config.nonsense/kanibako"`` beside ``nonsense: /srv/elsewhere`` really did
    root the store at ``/srv/elsewhere/kanibako`` (measured both sides, 2026-09-09).  The
    refusal was therefore narrower than spec §1 (*"The Layer-1 set is exactly the config
    keys in the table below"*) in one direction only: a bare ``nonsense`` was loud,
    ``config.nonsense`` silent — and the silent one was resolvable.
    ⚑ THE DECLARED SET IS :data:`~kanibako.settings.bootstrap.CONFIG_PATH_DEFAULTS`, the
    table ``resolve_config_paths`` itself iterates — so ACCEPTED HERE ⇒ RESOLVED THERE holds
    by construction, and a key added to §1 carries its own admission (P13).  It is the same
    six spellings as the keyspace's ``DECLARED_CONFIG_LEAVES``, and the two are pinned equal
    through the manifest (``test_manifest_conformance``'s ``TestKeySetConformance`` for the
    leaves, the ``bootstrap-path-defaults`` kinemata view for the table); this reader takes
    the Layer-1 table because Layer 1 is resolved by the flat resolver, NOT the keyspace
    pipeline (spec §1).

    ⚑ A ``config:`` with NOTHING under it is the created file's own state, not an error —
    ``write_global_config`` writes zero bytes, so absent and empty must agree.  A ``config:``
    holding a VALUE cannot mean anything and is refused rather than read as empty.
    """
    data = load_doc(path)
    settings_keys = _layer1_settings_keys(data)
    if settings_keys:
        raise ConfigError(ERR_CONFIG_LAYER1_SETTINGS % (path, "\n  ".join(settings_keys)))
    table = data.get(_LAYER1_TABLE)
    if table is None:
        return {}
    if not isinstance(table, dict):
        raise ConfigError(ERR_CONFIG_LAYER1_TABLE % (path, table))
    paths = _flatten_dotted(table, _LAYER1_TABLE)
    undeclared = sorted(key for key in paths if key not in CONFIG_PATH_DEFAULTS)
    if undeclared:
        raise ConfigError(ERR_CONFIG_LAYER1_UNDECLARED % (
            path, "\n  ".join(undeclared), ", ".join(sorted(CONFIG_PATH_DEFAULTS))))
    _refuse_null_paths(path, table, _LAYER1_TABLE, CONFIG_PATH_DEFAULTS)
    return paths


def system_path_set_values(settings_path: Path) -> dict[str, str]:
    """A SETTINGS file's ``system.*`` set-values, dotted — the Layer-2 half of the path tier.

    ⚑ ITS OWN READER since 2026-08-31.  This was ``load_config(path).config_paths`` — the
    very call the LAYER-1 read used, over one field that held ``config.*`` and ``system.*``
    together.  One function answering two layers' questions is what let each layer's file
    speak for the other; the walk here starts at the ``system:`` table, so ``system.`` is
    the only prefix it can produce.
    ⚑ NOT filtered to the path tier — that is :func:`~kanibako.settings.paths.load_system_config`'s
    own P13 job, and this file's ``system:`` table legitimately holds ``system.agent`` and
    the category families too.  That is also why the ``null`` refusal is scoped to
    :data:`SYSTEM_PATH_DEFAULTS`: ``system.agent: null`` means "no default agent" (spec §2b).
    ⚑ A NON-TABLE ``system:`` READS AS EMPTY HERE, where Layer 1 refuses its non-table
    ``config:`` (``ERR_CONFIG_LAYER1_TABLE``).  The tiers differ because this file is a
    keyspace tier: the launch resolve already refuses ``system: /x`` by name as a key that
    is not a key (spec §0), so a refusal here would be a second carrier.  Layer 1 is
    outside the keyspace, and no later read would catch it.
    """
    table = load_doc(settings_path).get("system")
    if not isinstance(table, dict):
        return {}
    _refuse_null_paths(settings_path, table, "system", SYSTEM_PATH_DEFAULTS)
    return _flatten_dotted(table, "system")


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
    """A settings-file scalar as the flat object carries it (bool or null)."""
    if isinstance(value, bool) or value is None:
        return value
    return str(value)


def _present_scalar_fields(path: Path) -> dict[str, object]:
    """The DECLARED box scalars PRESENT in a SETTINGS file — ``None`` is a VALUE, not a
    request for the default (§2h).

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
    files by ``settings_launch.load_merged_config``; a settings file's ``system.*`` path set-values by
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

    One recipe for ``settings_launch.fold_floor`` (every resolve),
    ``paths._narrow_box_scalar_cascade`` and ``config_interface._category_set_lookups``.
    """
    defaults = KanibakoConfig()
    floor: dict[str, object] = {}
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        value = getattr(defaults, field_name)
        # ⚑ A declared ``<None>`` is SUPPLIED as a present ``None`` (spec §2b
        # ``box.shell | <None>``) — never ``""``, which is a value, and which a
        # ``default_categories`` fold drops as a suppression, so ``@box.shell`` would
        # dangle.  A winning present ``None`` lands on the flat field as ``None``
        # (auto-detect), via ``settings_launch.resolve_box_scalars``.  ⚑ ``False`` is a VALUE and survives — ``False == ""`` is False.
        floor[dotted] = None if value == "" else value
    return floor


def _typed_box_scalar(defaults: KanibakoConfig, field_name: str, value: object) -> object:
    """Land a resolved box scalar on its field's own type — bool through the truth table.

    ⚑ The BOOL arm is selected off the DATACLASS DEFAULT, not a hand-kept name list, so a
    fourth scalar cannot be added without its coercion (``box.enable_vault``, 2026-08-29:
    a settings file stores ``false``, and ``str(False)`` is the truthy ``"False"``).

    ⚑ A ``<None>``-admitting field is selected off the SAME default — for one the
    declared default IS ``None``, the only case where ``str(None)`` would hand a
    consumer the program ``None`` (spec §2h).
    """
    if value is None and getattr(defaults, field_name) is None:
        return None
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
    ``paths.resolve_box_enable_vault``, which runs the real cascade and therefore honors the
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
    MOVED to ``paths.resolve_box_enable_vault``, where the workset tier is one cascade level
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
        # answer contradict ``paths.resolve_box_enable_vault``'s for the one command that
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
    """The stored ``workset.kuid`` at *path*, defaulting to :data:`kanibako.kuid.SENTINEL`.

    A ``workset`` holding a non-table value raises :class:`ConfigError`.
    """
    from kanibako import kuid

    if not path.exists():
        return kuid.SENTINEL
    data = load_doc(path)
    refuse_scalar_sections(path, ("workset",), data=data)
    value = data.get("workset", {}).get("kuid", kuid.SENTINEL)
    return str(value)


def read_workset_skip_kuid_check(path: Path) -> bool:
    """The stored ``workset.skip_kuid_check`` bool at *path*, defaulting to ``True`` (checking OFF).

    A ``workset`` holding a non-table value raises :class:`ConfigError`.
    """
    if not path.exists():
        return True
    data = load_doc(path)
    refuse_scalar_sections(path, ("workset",), data=data)
    return bool(data.get("workset", {}).get("skip_kuid_check", True))


def _split_config_key(flat_key: str) -> tuple[str, str]:
    """Split a flat config key into ``(section, key)``; no recognized prefix → an EMPTY section."""
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
        # Top-level scalar field (no recognized section prefix).
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
        # Top-level scalar field (no recognized section prefix).
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
    absent from the file and a present ``None`` are both not overrides
    (:func:`_present_scalar_fields`).
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

    ⚑ A LEAF WHOSE STORED SHAPE IS NOT A SCALAR IS STRINGIFIED BY THE MODULE THAT
    OWNS THE SHAPE (``agent_file.stored_leaf_display``), not by ``str()``; a bare
    ``str()`` printed the Python repr ``['--a', '--b']`` at every reader of
    ``system get run_args`` and ``system show``.

    ⚑⚑ AND A SCALAR RENDERS THROUGH ``config_io.render_stored_scalar``, FOR THE SAME REASON —
    THIS IS A ``get`` DOOR, not a ``show``-only one.  ``get_config_value``'s bare agent-setting
    branch answers straight out of this dict, so a second stringifier here is a second answer
    for one stored value: ``str()`` printed ``None`` for the §2h present-``None`` OMIT idiom and
    ``True`` for a bool the routed read spells ``true``.
    """
    if not path.exists():
        return {}
    return agent_settings_of(load_doc(path), agent_name)


def agent_settings_of(data: dict, agent_name: str) -> dict[str, str]:
    """:func:`read_agent_settings` over a settings doc already in hand.

    ⚑ FOR A READER THAT MUST JUDGE A FILE BY WHAT THE CASCADE READS FROM IT —
    ``config_interface.show_config`` hands in the reader's ``DISPLAY`` view,
    because re-reading the path would see an ``agent:`` table directional enforcement drops.
    """
    # ⚑ FUNCTION-SCOPE, AND IT MUST STAY THAT WAY: ``agent_file`` imports
    # ``agent_config``, which imports THIS module for ``AGENT_META_FILE``, so a
    # module-scope import here closes ``config → agent_file → agent_config → config``.
    # The idiom is this file's own (see the other deferred imports below).
    # ⚑ AND A TABLE-VALUED LEAF IS FLATTENED BY THE ONE SHOW WALK (``config_display.flatten_under``),
    # so an agent-tier category map prints ``caches./home/agent/c = uv`` like every other noun's
    # rows, never the dict repr ``caches = {'~/c/': ['uv']}``.
    from kanibako.settings.config_display import flatten_under

    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return {}
    out: dict[str, str] = {}
    for node in ("default", agent_name):
        sec = agent.get(node)
        if isinstance(sec, dict):
            out.update(flatten_under(f"agent.{node}.", sec))
    return out


def system_settings_path() -> Path:
    """THE system SETTINGS file (``@config.settings``), resolved from Layer-1 alone.

    ⚑ Path resolution only, deliberately NOT ``paths.load_std_paths``: that one
    RAISES when no Layer-1 file exists, which a pre-cascade reader may not do to a
    box that has never been set up.  ⚑ And NOT ``paths.load_system_config``: that
    resolves the primary workset's dir keys, which read this file through
    ``workset_dirkeys.early_repoint``.  ``spec_default_xdg_map``, so a reader on the
    detection side creates nothing.
    ⚑ The returned path need not exist — every reader here treats an absent file as
    "unset", which is exactly what a fresh install is.
    """
    from kanibako.settings.bootstrap import (XDG_DATA_HOME, XDG_SPEC_DEFAULTS)
    from kanibako.settings.paths import (layer1_set_values, resolve_config_paths,
                                         spec_default_xdg_map, xdg)

    data_home = xdg(XDG_DATA_HOME, XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    config = resolve_config_paths(
        layer1_set_values(user_config_file()), data_home=data_home, home=Path.home(),
        xdg_vars=spec_default_xdg_map(data_home),
    )
    return Path(config["config.settings"])


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


#: Where the helper-hub SPAWN BUDGET sits inside a settings document: the nested
#: ``system: helpers:`` table (spec §2g).  ⚑ ONE spelling, EXPORTED — the reader below
#: and the per-child document ``channels/helpers.py`` writes must not name two places.
#: ``config_keys._KEY_ROUTES`` carries the CLI verb's copy of the same slot — the
#: arrangement ``system.agent`` has had since it was declared.
SYSTEM_HELPERS_SECTION: "tuple[str, ...]" = ("system", "helpers")


def read_system_helpers(settings_path: Path | None) -> dict[str, int]:
    """The ``system.helpers.*`` SPAWN BUDGET leaves from a settings document; empty when unset.

    ⚑ *settings_path* is any document carrying a ``system`` tier — ``@config.settings``
    for a box's own budget, and the RO document a parent writes into a helper's home for
    the budget that helper was HANDED.  One shape, so one reader answers both.
    ⚑ A RAW reader, the third of this file's three (:func:`read_system_agent`,
    :func:`read_setup_completed`): ``kanibako box helper spawn`` decides whether it may
    spawn at all before any snapshot exists.
    🛑 A leaf that is not a whole number is REFUSED BY NAME.  Coercing it to a default
    would answer a budget question with a number the user never wrote — and the set-time
    guard (``config_keys.KEY_TYPES``) cannot reach a hand-edited file.
    ⚑ The leaf set is the KEYSPACE's own (P13), not a second list here; the lazy import
    is this file's idiom for the ``keystore`` cycle its module docstring names.
    """
    from kanibako.settings.settings_keyspace import DECLARED_SYSTEM_HELPERS_LEAVES

    if settings_path is None or not settings_path.exists():
        return {}
    node: object = load_doc(settings_path)
    for section in SYSTEM_HELPERS_SECTION:
        if not isinstance(node, dict):
            return {}
        node = node.get(section)
    if not isinstance(node, dict):
        return {}
    out: dict[str, int] = {}
    for leaf in sorted(DECLARED_SYSTEM_HELPERS_LEAVES):
        if leaf not in node:
            continue
        try:
            out[leaf] = int(node[leaf])
        except (TypeError, ValueError):
            raise ConfigError(
                f"system.helpers.{leaf} in {settings_path} must be a whole number, "
                f"got {node[leaf]!r}"
            ) from None
    return out


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


def resolve_agent(
    *,
    explicit_agent: str | None,
    requested: str | None = None,
    project_path: Path | None = None,
) -> str:
    """Validate the effective agent name against the installed set, or REFUSE (spec §2b).

    ⮕ **P7: the CASCADE moved out** — what stays here is what is NOT a key.

    🛑 **THERE IS NO INSTALLED-AGENT COUNT RULE** (retired 2026-09-19, his ruling). A
    name that resolved is validated; a name that did NOT resolve is a REFUSAL naming
    ``kanibako setup``, whatever the count — **one installed agent does not make the
    choice for the user.** Present-``None`` never reaches here: it is a DIFFERENT
    refusal, raised at the selection seam (``agent_select.select_agent``), because the
    two states mean different things and must not print the same sentence.
    """
    # ⚑ Lazy: kanibako.targets imports paths/config indirectly (cycle risk).
    from kanibako.agent_ref import (
        ADDRESSABLE_PSEUDO_AGENTS, parse_agent_address, with_harness,
    )
    from kanibako.errors import AgentNotInstalledError, AgentUnsetError
    from kanibako.identifiers import find_identifier
    from kanibako.install_method import install_command
    from kanibako.targets import discover_targets

    # ⚑⚑ THE FIRST *PRESENT* TIER RESOLVES — never the first non-EMPTY one.
    # ABSENCE already has its own spelling in BOTH arguments: no ``--agent`` at
    # all, and ``__MISSING__`` at the selection key, each arriving here as
    # ``None``.  A present ``""`` is therefore a VALUE, not an absence — spec §2h
    # ("present-``None``, terminal ``""`` (≠ unset), and the COPY-disable
    # sentinel" are three distinct idioms).  Reading it as absence invented a
    # FOURTH meaning for the one that already means something, and answered it by
    # launching whatever the next tier said: a typed ``--agent ""`` took the
    # cascade's agent, a stored ``system.agent: ""`` took the then-live
    # installed-count rule's — silently, either way.  Every OTHER illegal ref at
    # either tier has always refused here; the blank one escaped through an ``or``.
    raw_resolved = explicit_agent if explicit_agent is not None else requested

    if raw_resolved is not None:
        # ⚑ Canonicalize + validate the ref shape; the HARNESS is what must be
        # installed — NOT the composite node-name (a persona segment is free-form).
        # ⚑ It also STRIPS, and it OWNS every way a ref can be illegal — charset,
        # pseudo-agent reservation, empty-after-strip — so a blank is refused by
        # ITS message rather than by a second predicate spelled here.  The
        # ``ConfigError`` is a ``KanibakoError``: ``cli.py`` flattens it to one
        # ``Error:`` line with the offending value first.
        # ⚑ THE ADDRESS GRAMMAR, so the SHELL PSEUDO-AGENT IS SELECTABLE BY NAME
        # (spec §2b) though no agent, persona or harness may CLAIM the name (§2d).
        # It is the built-in occupying its own slot ([R175]), so it resolves
        # WITHOUT consulting the installed set.  ``default`` stays a reservation
        # refusal: the any-agent tier is not a launchable agent.
        node, harness = parse_agent_address(raw_resolved)
        if node in ADDRESSABLE_PSEUDO_AGENTS:
            return node
        # ⚑⚑ THE INSTALLED SET IS READ *ONLY INSIDE THIS BRANCH*, AND THAT IS THE
        # POINT (P3/P4): it answers "is this NAME installed?" and is not in scope on
        # the refusal path below, so the installed-agent count rule cannot be
        # reintroduced there without re-adding this call — which a reader would see.
        installed = set(discover_targets(project_path).keys())
        # ⚑⚑ THIS IS THE HOP WHERE A NAME BECOMES A NODE ([R173]).  ``system.agent``
        # and ``--agent`` carry a NAME — the plugin's declared case, or whatever the
        # user typed — while the registry is keyed by NODE, so the match is case-blind
        # and what comes back is the NODE.  Substituting it is what makes
        # ``--agent Claude`` reach the ``claude`` plugin AND key ``agents/claude/``:
        # returning the typed spelling instead would spell one agent's store and
        # cascade slot two ways.  ⚑ Only the HARNESS segment is replaced — a persona
        # segment is the user's and is not this ruling's to touch.
        found = find_identifier(harness, installed)
        if found is not None:
            return with_harness(node, found)
        raise AgentNotInstalledError(
            f"Agent '{harness}' is not installed. Install it with:\n"
            f"  {install_command(f'kanibako-agent-{harness}')}\n"
            f"Or run 'kanibako agent list' to see installed agents."
        )

    # Nothing resolved at ANY tier ⇒ @system.agent is UNSET: setup has never chosen
    # an agent (spec §2b).  ⚑ ``installed`` is deliberately NOT consulted — the old
    # count rule launched the single installed agent implicitly, and that is the
    # behavior this refusal replaces.
    raise AgentUnsetError(
        "No default agent is configured (system.agent is unset).\n"
        "Choose one with:\n"
        "  kanibako setup\n"
        "Or name one for a single run with '--agent <name>'.\n"
        "'kanibako shell' reaches the box's container without an agent."
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


def _flatten_leaves(data: dict, prefix: str = "") -> dict[str, object]:
    """Flatten a nested dict into DOTTED-key form, each leaf AS STORED.

    ⚑ THE ONE WALK: :func:`_flatten_dotted` is this with its leaves stringified, so the keys
    one names are the keys the other names. The raw leaf is for the SETTINGS files'
    ``config:`` refusal and its stored view (``settings_assemble.stored_config_entries``),
    which reads that table exactly as the Layer-1 read would and DISPLAYS the values —
    ``str()`` would print a stored ``null`` as ``None``, a spelling the file never held.
    ⚑ ``str(k)`` ON THE UNPREFIXED ARM: a YAML key need not be a string, and only the
    f-string arm stringified one — so a top-level ``1: x`` handed an ``int`` to callers
    that sort and join (:func:`_layer1_settings_keys`).
    """
    out: dict[str, object] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten_leaves(v, key))
        else:
            out[key] = v
    return out


def _flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into DOTTED-key form, stringifying scalar leaves
    (:func:`_flatten_leaves`, the walk).

    ⚑ NOT a scope-category helper — its callers are the Layer-1 ``config:`` read, the
    Layer-2 ``system:`` path-tier read, and the Layer-1 refusal that names its keys.
    ⚑ A ``null`` leaf becomes the string ``"None"``: both path reads call
    :func:`_refuse_null_paths` first.
    """
    return {key: str(v) for key, v in _flatten_leaves(data, prefix).items()}


def null_path_keys_error(
    path: Path, keys: Iterable[str], *, cure: str = ERR_CONFIG_NULL_PATH_CURE,
    head: "str | None" = None, read_head: str = ERR_CONFIG_NULL_PATH_HEAD,
) -> "str | None":
    """THE carrier for "these keys are ``null``" (spec §2a) — ``None`` when none are.

    ⚑ ONE CARRIER, EVERY DOOR (P10): both path tiers raise through it via
    :func:`_refuse_null_paths` and the ``set`` door calls it.  Every offender is named in
    full, sorted.

    ⚑ TWO LEADS, TWO ARITIES — which is why they are two parameters and not one.  The
    read-time lead names the file that HOLDS the lines, so it takes ``(path, keys)``; a
    door that wrote none cannot name a file, so *head* takes ``(keys)`` alone.
    """
    nulls = sorted(keys)
    if not nulls:
        return None
    keys_block = "\n  ".join(nulls)
    lead = read_head % (path, keys_block) if head is None else head % keys_block
    return lead + cure


def refuses_null_path_key(canonical: str) -> bool:
    """True iff the LAUNCH refuses a present ``null`` at *canonical* (spec §2a).

    ⚑ THE MEMBERSHIP, DERIVED FROM THE READERS (P13), not a second list beside them.  Both
    path tiers refuse every key of their own defaults table (:func:`_refuse_null_paths`),
    so a key added to either lands here too; the ``config.*`` rows are ``set: file`` with
    no CLI write route, carried only for the WHOLE rule.

    🛑 THE WORKSET DIR KEYS ARE A MIX, so the membership is the readers' answer and not
    "is this a path key".  Two refuse a null on their own reader, and no table carries
    either: ``workset.boxes`` (leaf :data:`BOXES_PATH`) and ``workset.registry``.  The
    rest MEAN something: ``workset.workspaces: null`` is "no workspace dir" (spec §2c),
    ``workset.logs: null`` "no logs dir".
    """
    return (
        canonical in SYSTEM_PATH_DEFAULTS
        or canonical in CONFIG_PATH_DEFAULTS
        or canonical in (f"workset.{BOXES_PATH}", "workset.registry")
    )


def usable_box_store_value(value: object) -> bool:
    """True iff the LAUNCH can use *value* at the box store — the box root's own test.

    ⚑ THE VALUE HALF, stated once: the launch reads it for every root it dereferences,
    so a set door must not spell the test again. 🛑 EXISTENCE + LEAF, NOT ABSOLUTENESS
    — whitespace-only is NOT a refusal.
    """
    return isinstance(value, str) and value != "" and not value.endswith("/")


def refuses_box_store_value(canonical: str, value: object) -> bool:
    """True iff the LAUNCH refuses *value* AT THE BOX STORE KEY (spec §0, §2c).

    ⚑ THE KEY MEMBERSHIP, and the ``workset.boxes`` key check — the launch reads two keys
    with one test, so only the set door asks which key. It adds no judgment of its own.
    """
    if canonical != f"workset.{BOXES_PATH}":
        return False
    return not usable_box_store_value(value)


def chain_bad_entries(
    value: object, bad: Iterable[str], *, key: str, stored: "Callable[[str], object]",
) -> list[str]:
    """The *bad* entries the edited *value* of *key* REACHES on its own ``@``-chain (spec §2a).

    ⚑ THE SPLIT, STATED ONCE.  A bad entry in a file the command reads has two arms: one the
    edited value's own chain depends on, which is a HARD error ``--force`` does not override,
    and one it does not, which is an error unless ``--force``.  Only the first is a question
    about the value; the second is a question about the file, and belongs to the door.

    ⚑ TRANSITIVE, through DECLARED keys only.  The walk follows a ref to a value ``stored``
    reads, so a chain of two hops (``@a.b`` whose value is ``@c.d``) reaches ``c.d``.  A ref
    that is itself bad ends the walk there — its value is not a key's value to follow.
    *stored* answers ``None`` for a name no file holds, and the walk stops.

    ⚑ A TEXT value (:func:`~kanibako.settings.settings_configset.holds_verbatim_text`) has
    no chain: neither the edited value of a text *key* nor the stored value of a text key a
    ref names is scanned, so the ``@host`` in ``https://user:key@host/v1`` is no ref.

    ⚑ A WORKLIST, NOT RECURSION, so a ``@``-chain that loops back on itself terminates on
    the SEEN set.  A self-reference is therefore a chain that reaches itself once.
    """
    from kanibako.settings.settings_configset import holds_verbatim_text, scan_tokens

    if not isinstance(value, str) or not value or holds_verbatim_text(key):
        return []
    remaining = set(bad)
    reached: list[str] = []
    seen: set[str] = set()
    pending = [value]
    while pending and remaining:
        try:
            refs, _vars = scan_tokens(pending.pop())
        except ValueError:
            continue  # malformed: the value's own door refuses that
        for ref in refs:
            if ref in remaining:
                reached.append(ref)
                remaining.discard(ref)
            elif ref not in seen and not holds_verbatim_text(ref):
                seen.add(ref)
                nxt = stored(ref)
                if isinstance(nxt, str) and nxt:
                    pending.append(nxt)
    return reached


def refuses_null_box_scalar(canonical: str) -> bool:
    """True iff the LAUNCH refuses a present ``null`` at the box scalar *canonical* (spec §2b).

    ⚑ DERIVED FROM THE DECLARED DEFAULT, and that is the whole test: a box scalar whose own
    default IS ``<None>`` gives a null something to SAY — ``box.shell`` auto-detects
    (spec §2b) — so it is honored.  Every other box scalar's default is a VALUE, and
    spec §2h's "the consumer reads ``None``, never the key's default" has no consumer to
    hand it to: a ``box.image`` would be the string ``"None"`` and a bool one would read
    ``False`` — a value the file never held, chosen by the reader.

    🛑 NOT ``canonical in _BOX_SCALAR_FIELDS``, which names the WHOLE overlay and holds
    ``box.shell`` too.  A fifth box scalar lands refused here without a list edit, and is
    honored the day its own declared default admits a ``<None>``.
    """
    if canonical not in _BOX_SCALAR_FIELDS:
        return False
    return getattr(KanibakoConfig(), _BOX_SCALAR_FIELDS[canonical]) is not None


def _refuse_null_paths(path: Path, table: dict, prefix: str, path_keys: Iterable[str]) -> None:
    """Refuse a ``null`` at any of *path_keys* in *table*, naming *path* and the keys."""
    leaves = _flatten_leaves(table, prefix)
    error = null_path_keys_error(
        path, (key for key in path_keys if key in leaves and leaves[key] is None),
    )
    if error is not None:
        raise ConfigError(error)


def system_path_ref_error(canonical: str, value: "str | None") -> "str | None":
    """THE refusal for a ``system.*`` path value pointing outside the system path tier, or ``None``.

    ⚑ THE SCOPE IS THE LAUNCH'S OWN SPLIT, never a list written here.
    ``paths._resolve_system_path_keys`` resolves a ``system.*`` path value through ONE
    lookup that consults the ``config.*`` foundation and the single ``system`` level and
    nothing else, so a ``@box.*`` ref is unresolvable there — the launch reports it as
    ``Unknown @-reference`` at EVERY seam.  This function expands the value through
    :func:`kanibako.settings.config_interface._path_tier_split`, the same
    ``(config.*`` foundation, ``system.*`` floor) that lookup resolves against, and
    refuses a ref that split cannot see (system-design "Ordering rule": a key may
    depend only on key sets preceding it, or on its own set).  A
    ``@config.*`` key or a system PATH key is inside that split and passes; any other
    ``@system.*`` ref (``@system.agent``) is not, and is refused.
    ⚑ THE MEMBERSHIP IS :data:`SYSTEM_PATH_DEFAULTS` — the very table ``paths.py``
    resolves the tier from (P13), so a key added to the tier is judged here with no edit.
    ``config.*`` keys are ``set: file`` with no CLI write route and are carried by the
    read-time doors alone.
    ⚑ ``_unusable_store_root_error`` builds the same split for a DIFFERENT question
    (whether a ``system.state`` store root is USABLE) and RAISES on a miss. This one
    RECORDS a miss and returns ``""`` so expansion completes and one verdict covers
    every ref in the value; the two are deliberately not one helper.
    ⚑ THE UNKNOWABLE IS NOT REFUSED: no value, a tier that will not build, a malformed
    token or a shape another door owns all answer ``None`` — a set door that refused
    everything on a broken tier would be worse than the value it caught.
    """
    if not value or canonical not in SYSTEM_PATH_DEFAULTS:
        return None
    from kanibako.settings.config_interface import _path_tier_split, _set_time_ctx
    from kanibako.settings.settings_resolve import expand_expr

    try:
        config_foundation, path_floor = _path_tier_split()
    except Exception:
        return None
    misses: list[str] = []

    def _lookup(ref: str, chain: "tuple[str, ...]" = ()) -> str:
        got = config_foundation.get(ref) if ref.startswith("config.") else path_floor.get(ref)
        if got is None:
            misses.append(ref)
            return ""
        return str(got)

    try:
        expand_expr(value, space="host", ctx=_set_time_ctx(config=config_foundation),
                    lookup=_lookup)
    except Exception:
        return None
    if not misses:
        return None
    return ERR_CONFIG_PATH_REF_SCOPE % (canonical, value, misses[0])


def _resolution_set(dotted: str) -> "str | None":
    """The :data:`~kanibako.settings.kb_store.RESOLUTION_ORDER` set *dotted* belongs to, or ``None``."""
    from kanibako.settings.kb_store import RESOLUTION_ORDER

    head, _, rest = dotted.partition(".")
    name = f"meta.{rest.partition('.')[0]}" if head == "meta" else head
    return name if name in RESOLUTION_ORDER else None


def ref_order_error(canonical: str, value: "str | None") -> "str | None":
    """THE set-door refusal of an ``@``-ref naming a set resolved after *canonical*'s, or ``None``.

    System-design "Ordering rule": a key may depend only on key sets PRECEDING it, or on its
    own set. The verdict reads the SPELLING, so it holds whether or not this command's cascade
    holds the referent. A key or ref outside :data:`~kanibako.settings.kb_store.RESOLUTION_ORDER`
    (``pref.*``, an undeclared namespace) and a malformed value answer ``None``: other doors
    own them. A ``system.*`` path key answers ``None`` too, since
    :func:`system_path_ref_error` holds it to the narrower path tier.
    """
    if not value or canonical in SYSTEM_PATH_DEFAULTS:
        return None
    from kanibako.settings.kb_store import RESOLUTION_ORDER
    from kanibako.settings.settings_configset import scan_tokens

    key_set = _resolution_set(canonical)
    if key_set is None:
        return None
    try:
        refs, _vars = scan_tokens(value)
    except ValueError:
        return None
    rank = RESOLUTION_ORDER.index(key_set)
    for ref in refs:
        ref_set = _resolution_set(ref)
        if ref_set is not None and RESOLUTION_ORDER.index(ref_set) > rank:
            return ERR_CONFIG_REF_ORDER % (canonical, value, ref, ref_set, key_set)
    return None
