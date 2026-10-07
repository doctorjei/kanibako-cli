"""The NO-SNAPSHOT resolver for the workset dir keys — ONE route, ONE grammar.

The keys in :data:`WORKSET_EARLY_KEYS` are read
on the DETECTION / paths side, which runs BEFORE the per-launch KeyStore snapshot exists — it is the pass
that FINDS the workset the snapshot will later be built for.  So it cannot call
:func:`kanibako.settings.settings_expand.expand`, which needs that snapshot.

⚑⚑ IT STILL MUST NOT GROW A SECOND GRAMMAR.  Files store entries UNRESOLVED (spec
``:214`` — ``@``-refs and ``$XDG``/``~`` verbatim), so every one of these values may
carry a token, and the spec's own per-mode DEFAULT for the layout keys is
``@meta.workset.path/<leaf>`` (the two vault arms take a TWO-SEGMENT leaf,
``vault/ro`` and ``vault/rw``).  A resolver that merely ``expanduser()``-ed the string
turned that documented default into a literal directory named ``@meta.workset.path``
— silently relocating the box store, while the launch snapshot resolved the same key
correctly.  Two carriers, two answers.

⚑ What this module does instead: it is a THIRD CALLER of the single expression scanner
:func:`~kanibako.settings.settings_resolve.expand_expr` (seam S25), with a lookup
NARROWED to the references that are knowable without a snapshot: :data:`WORKSET_PATH_REF`,
whose value is the workset root the caller already holds, the scope's name, the ``system.*``
paths, and the OTHER workset early keys, which this same route resolves (the spec's own
channel defaults are ``@workset.channelroot/<leaf>``, a same-set reference the ordering
rule allows).  The scanner's chain guards a cycle among them.  ``~`` and ``$XDG_*``
expand host-side exactly as they do at launch.  Every other reference is REFUSED BY NAME.
A refusal that names the key and the token is a correct answer to "this cannot be
resolved yet"; a directory called ``@config.registry`` is not.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

from kanibako.errors import ConfigError
from kanibako.settings.agent_config import (
    ambiguous_path_value_error,
    is_unambiguous_path_value,
)
from kanibako.settings.bootstrap import (
    BOXES_PATH, LOGS_PATH, SYSTEM_PATH_DEFAULTS, WORKSPACES_PATH,
)
from kanibako.settings.config import WORKSET_META_FILE, ref_order_error
from kanibako.settings.config_io import load_doc
from kanibako.settings.settings_keyspace import DECLARED_WORKSET_CHANNEL_LEAVES
from kanibako.settings.settings_resolve import (
    UNSET, ResolveCtx, SettingsError, _Unset, expand_expr, literal_expr,
)

#: The workset root, which every caller of :func:`resolve_workset_dir_key` already has in
#: hand.  It anchors the keys' spec-declared defaults, so the documented value
#: resolves here without the snapshot the rest of the keyspace needs.
WORKSET_PATH_REF = "meta.workset.path"
#: The scope's workset name: ``EarlyScope.workset_name``.
WORKSET_NAME_REF = "meta.workset.name"

#: The workset EARLY keys (under ``workset.``): the keys this route reads, so the ones that
#: may depend only on :data:`_USABLE_REFS` (system-design "Ordering rule").
WORKSET_EARLY_KEYS: frozenset[str] = frozenset({
    WORKSPACES_PATH, BOXES_PATH, LOGS_PATH, "channelroot", "registry", "canon", "template",
    "vault_ro", "vault_rw",
    *(f"channels.{leaf}" for leaf in DECLARED_WORKSET_CHANNEL_LEAVES),
})


def _stored_repoint(
    doc: Mapping[str, Any] | None, key: str, *, where: Path | None = None,
) -> str | None | _Unset:
    """The RAW ``workset.<key>`` stored in *doc*, at the key's routed slot.

    A string; ``None`` for a PRESENT ``<None>``; :data:`UNSET` when absent, empty, or
    *doc* is ``None``.

    ⚑ IT ASKS :func:`~kanibako.settings.settings_categories.is_path_key_value` BEFORE THE
    ``str()`` BELOW: ``str()`` is too late, so a ``{x: null}`` reached
    :func:`resolve_workset_dir_key` as the repr ``"{'x': None}"`` — a STRING, which the
    bare-relative arm judged as a path the user never wrote.  Through the ONE §2a carrier;
    *where* names the file when the caller holds it.
    """
    from kanibako.settings.agent_config import ambiguous_path_shape_error
    from kanibako.settings.config_keys import _KEY_ROUTES
    from kanibako.settings.settings_categories import is_path_key_value
    from kanibako.settings.settings_resolve import SettingsError

    sections, slot = _KEY_ROUTES[f"workset.{key}"]
    node: object = doc
    for section in sections:
        if not isinstance(node, Mapping):
            return UNSET
        node = node.get(section)
    if not isinstance(node, Mapping):
        return UNSET
    value = node.get(slot, UNSET)
    if value is None:
        return None
    if value is UNSET or not value:
        return UNSET
    if not is_path_key_value(value):
        raise SettingsError(ambiguous_path_shape_error(
            f"workset.{key}", value, where=str(where) if where is not None else None,
        ))
    return str(value)


def early_repoint(
    workset_root: Path, workset_settings: Mapping[str, Any] | None, key: str,
    *, early: EarlyScope,
) -> tuple[str | None | _Unset, Path]:
    """The RAW ``workset.<key>`` this route resolves, and the file that carries it.

    The cascade ``system < workset``: *workset_settings*, the root's own ``workset.yaml``,
    wins, a PRESENT ``<None>`` included (spec §2h); only a key it does not carry falls
    through to the system tier, *early*'s record.  :data:`UNSET` when neither tier carries
    it, so the key takes its per-mode default.  A refusal names the file the value came
    from, else the root's own.
    """
    if key not in WORKSET_EARLY_KEYS:
        raise ValueError(f"workset.{key} is not a workset early key")
    own_file = workset_root / WORKSET_META_FILE
    value = _stored_repoint(workset_settings, key, where=own_file)
    if value is not UNSET:
        return value, own_file
    value = early.system.tier.get(f"workset.{key}", UNSET)
    if isinstance(value, str):
        _refuse_unanchored(workset_settings, key, value, early=early, where=own_file)
    return value, (own_file if value is UNSET else early.system.file)


def _refuse_unanchored(
    workset_settings: Mapping[str, Any] | None, key: str, value: str, *, early: EarlyScope,
    where: Path | None = None,
) -> None:
    """Keyspec §0 "Per-owner resources" at the read door: refuse an inherited *value* of
    ``workset.<key>`` that reaches no owner identity, naming the file.

    The set door's judgment, :func:`~kanibako.settings.config.reaches_identity` in every box
    mode, through the raw cascade values, so the two doors agree on what is a collision.
    """
    from kanibako.settings.config import identity_gap, reaches_identity
    from kanibako.settings.config_keys import KEY_OWNERS
    from kanibako.settings.messages import ERR_PER_OWNER_READ, PER_OWNER_SET_WORDS
    from kanibako.settings.paths import BoxMode

    dotted = f"workset.{key}"
    owner = KEY_OWNERS[dotted]
    if owner == "shared":
        return

    def stored(ref: str) -> object:
        referent = ref.removeprefix("workset.")
        if referent == ref or referent not in WORKSET_EARLY_KEYS:
            return None
        raw = _stored_repoint(workset_settings, referent, where=where)
        return early.system.tier.get(ref) if isinstance(raw, _Unset) else raw

    if all(reaches_identity(value, owner, mode, key=dotted, stored=stored) for mode in BoxMode):
        return
    identity, file_owner = PER_OWNER_SET_WORDS[owner]
    gap = identity_gap(value, owner, None, key=dotted, stored=stored)
    raise ConfigError(ERR_PER_OWNER_READ % (
        dotted, value, early.system.file, gap.who, identity, gap.sharers,
        dotted, gap.cure, dotted, dotted, file_owner,
    ))


def refuse_inherited_per_owner(
    workset_root: Path, early: EarlyScope, *, doc: Mapping[str, Any] | None | _Unset = UNSET,
    exclude: Collection[str] = (),
) -> None:
    """Refuse, before a verb changes anything, any per-owner early key *workset_root*'s
    workset inherits from the system tier with a value that reaches no owner identity.

    Runs :func:`early_repoint` over every per-owner key in :data:`WORKSET_EARLY_KEYS` less
    *exclude*; raises its :class:`~kanibako.errors.ConfigError`.  *doc*: the own file's
    document, if read.  ⚑ *exclude* is what a door that reaches only a SUBSET of the keys
    passes — the keys it resolves are the door's own knowledge, so the walk stays here.
    """
    from kanibako.settings.config_keys import KEY_OWNERS

    if isinstance(doc, _Unset):
        doc = load_doc(workset_root / WORKSET_META_FILE)
    for key in sorted(WORKSET_EARLY_KEYS):
        if key in exclude:
            continue
        if KEY_OWNERS[f"workset.{key}"] != "shared":
            early_repoint(workset_root, doc, key, early=early)


@dataclass(frozen=True)
class EarlySystem:
    """The EARLY SYSTEM TIER as DATA — read once, passed down, never re-read.

    ``tier``
        The raw ``workset.<key>`` values the system settings file carries, dotted and stored
        as written; a present null is ``None``.  A key the file does not state is OMITTED, so
        ``k in tier`` means "this file states it" — the same line :func:`_stored_repoint`
        draws with :data:`UNSET`.
    ``file``
        The system settings path: the file a refusal names.
    ``system_paths``
        The resolved ``system.*`` tier, dotted, each value a
        :func:`~kanibako.settings.settings_resolve.literal_expr`, or ``None`` for a null key —
        the same shape and the same values as :func:`~kanibako.settings.paths.system_path_floor`.  EMPTY when
        ``system_refusal`` is set: those values were dropped, so there is nothing here to name.
    ``system_refusal``
        The text of a ``system:``-table refusal that TOLERANCE dropped, else ``None``.  ⚑ Set
        only where the document LOADED and its ``system:`` table was refused, or by the system
        set door when ``std`` failed: there it holds that failure's text, whatever its cause,
        and ``system_paths`` is likewise empty.  A document that does not load empties the
        whole tier instead, and carries no refusal text — the two tolerance arms are
        deliberately not the same thing.
    """

    tier: dict[str, str | None]
    file: Path
    system_paths: dict[str, str | None]
    system_refusal: str | None = None


class EarlyScope(NamedTuple):
    """What an early reader needs to resolve without a launch snapshot: the tier, and the name.

    ``workset_name`` rides beside the tier so a reader never has to go find it — from
    ``Workset.name``, the registry key, or the reserved partition name of the mode.
    """

    system: EarlySystem
    workset_name: str


def early_tier(doc: Mapping[str, Any] | None) -> dict[str, str | None]:
    """Every ``workset.*`` EARLY value *doc* carries, dotted — the system tier, as data.

    Built on :func:`_stored_repoint`, the ONE extractor, so this and the cascade read the same
    slot the same way.  A key the document does not state is OMITTED (``UNSET`` is not a
    value); a present null is ``None``.
    """
    tier: dict[str, str | None] = {}
    for key in WORKSET_EARLY_KEYS:
        value = _stored_repoint(doc, key)
        if isinstance(value, _Unset):
            continue
        tier[f"workset.{key}"] = value
    return tier


def early_system(set_values: Mapping[str, str | None],
                resolved: Mapping[str, Path],
                *, system_refusal: str | None = None) -> EarlySystem:
    """Build the record from the merged set-values and the resolved tier.  PURE — reads no file.

    *set_values* carries the early tier among its ``workset.`` entries (see
    :func:`~kanibako.settings.paths._path_tier_set_values`); *resolved* is the resolved path
    tier, which supplies both ``config.settings`` — the file a refusal names — and every
    ``system.*`` value.
    """
    return EarlySystem(
        tier={k: v for k, v in set_values.items() if k.startswith("workset.")},
        file=resolved["config.settings"],
        # ⚑ A null ``system.*`` key is ABSENT from *resolved* and carried as ``None``,
        # exactly as :func:`~kanibako.settings.paths.system_path_floor` carries it.
        system_paths=(
            {} if system_refusal is not None
            else {key: (None if (value := resolved.get(key)) is None
                        else literal_expr(str(value)))
                  for key in SYSTEM_PATH_DEFAULTS}
        ),
        system_refusal=system_refusal,
    )


def _host_ctx() -> ResolveCtx:
    """The host-side expansion namespace for a pre-snapshot resolve — ``~`` and ``$XDG_*``.

    ⚑ ``spec_default_xdg_map`` and NOT ``host_xdg_map``: this runs inside the ancestor
    WALK, on directories that may not be worksets at all, and ``host_xdg_map`` adds
    ``XDG_RUNTIME_DIR``, whose fallback can mkdir a directory and warn.  Detection must
    not have side effects.  ``$AGENT`` / ``$WORKSET`` are deliberately unset — neither
    is known before the snapshot, so both refuse rather than resolve to a guess.
    """
    from kanibako.settings.paths import spec_default_xdg_map

    return ResolveCtx(
        agent_name=None, workset_name=None,
        host_home=str(Path.home()), xdg=spec_default_xdg_map(None),
    )


def resolve_workset_dir_key(
    workset_root: Path, repoint: str | None, default_leaf: str, *, key: str,
    where: Path | None = None, standalone: bool | None = None,
    workset_settings: Mapping[str, Any] | None = None, early: EarlyScope,
) -> Path:
    """Resolve the ``workset.<key>`` *repoint* (or its ``<root>/<default_leaf>`` default).

    *repoint* is the RAW value as stored: it may carry ``@``-refs, ``$XDG_*`` or ``~``.
    *where* is the file a refusal names; it defaults to *workset_root*'s ``workset.yaml``.
    *standalone* is the box mode the caller reads *key* in (``None``: not known); it picks
    the declared default of an unset referent (see :func:`_referent_value`).
    *workset_settings* is the root's ``workset.yaml`` document the caller read (``None``: no
    file); a referent is read from it, then from the system tier (*early*, as
    :func:`early_repoint` takes it).
    *key* must be in :data:`WORKSET_EARLY_KEYS`; any other raises :class:`ValueError`.
    An unset repoint takes the default leaf under *workset_root*.  Raises
    :class:`~kanibako.settings.settings_resolve.SettingsError`, naming the key, the file
    and the token, when the value cannot be resolved without the launch snapshot.

    ⚑⚑ A BARE-RELATIVE *repoint* IS REFUSED, NOT ANCHORED ([R147], 2026-08-29).  It
    used to anchor under *workset_root*, and this seam's own refusal text used to OFFER
    that form.  The reason it cannot: the reason to set one of these keys AT ALL is to
    move the directory OFF its ``@meta.workset.path/<leaf>`` default, so "keep it with
    the workset" assumes precisely the intent the user is overriding — and the guess is
    not a confusing message, it is a directory created in the wrong place that then
    holds data.  The root-relative reading stays expressible; it has to be SAID.
    """
    if key not in WORKSET_EARLY_KEYS:
        raise ValueError(f"workset.{key} is not a workset early key")
    if where is None:
        where = workset_root / WORKSET_META_FILE
    if not repoint:
        return workset_root / default_leaf

    if not is_unambiguous_path_value(repoint):
        # ⚑ TESTED ON THE STORED SPELLING, and BEFORE the expand, so this seam and the
        # Layer-1/2 one (``paths._refuse_bare_relative``) ask the ONE question [R147]
        # asks — "is this a legal value to have written?" — rather than two variants of
        # it.  A post-expansion absoluteness test would agree here on every reachable
        # input and disagree in the message, which quotes the value the user typed.
        raise SettingsError(
            ambiguous_path_value_error(
                f"workset.{key}", repoint,
                anchor=str(workset_root), anchor_ref=f"@{WORKSET_PATH_REF}",
                where=str(where),
            )
        )

    order_err = ref_order_error(f"workset.{key}", repoint)
    if order_err is not None:
        # The set door's ordering verdict, for a value written by hand.
        raise SettingsError(f"{where}: {order_err}")

    try:
        expanded = _expand_early(
            workset_root, workset_settings, repoint, key=key, standalone=standalone,
            chain=(f"workset.{key}",), early=early,
        )
    except SettingsError as exc:
        raise SettingsError(
            f"workset.{key} is set to {repoint!r} in "
            f"{where}, which cannot be resolved: {str(exc).rstrip('.')}. "
            f"Use an absolute path, '~', '$XDG_*', or {_USABLE_REFS}; a "
            f"LITERAL '$', '~' or '@' in a directory name must be "
            f"backslash-escaped."
        ) from exc

    return Path(expanded)


#: The references a workset early key may use, as a refusal names them.
_USABLE_REFS = (
    f"'@{WORKSET_PATH_REF}' (this workset's root), '@{WORKSET_NAME_REF}' (its partition "
    f"name), a system path ('@system.channels.mailboxes', …), or another workset early key "
    f"('@workset.boxes', '@workset.channelroot', …)"
)


def _expand_early(
    workset_root: Path, doc: Mapping[str, Any] | None, value: str, *, key: str,
    standalone: bool | None, chain: tuple[str, ...], early: EarlyScope,
) -> str:
    """Expand *value* with the references knowable before the snapshot; *chain* guards cycles."""
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        if ref == WORKSET_PATH_REF:
            return str(workset_root)
        if ref == WORKSET_NAME_REF:
            return early.workset_name
        if ref in SYSTEM_PATH_DEFAULTS:
            return expand_expr(
                _system_path(ref, early.system), space="host", ctx=_host_ctx(), lookup=lookup,
                chain=chain,
            )
        referent = ref.removeprefix("workset.")
        if referent != ref and referent in WORKSET_EARLY_KEYS:
            return _referent_value(
                workset_root, doc, referent, key=key, standalone=standalone, chain=chain,
                early=early,
            )
        raise SettingsError(
            f"'@{ref}' cannot be resolved here: this key is read before the launch "
            f"snapshot exists, so it may reference only {_USABLE_REFS}"
        )

    return expand_expr(value, space="host", ctx=_host_ctx(), lookup=lookup, chain=chain)


def _system_path(ref: str, system: EarlySystem) -> str:
    """The stored expression of ``@<ref>``, a ``system.*`` path: the record's, never a re-read."""
    if system.system_refusal is not None:
        raise SettingsError(f"'@{ref}' cannot be read: {system.system_refusal}")
    value = system.system_paths.get(ref)
    if value is None:
        raise SettingsError(f"'@{ref}' is null in {system.file}, so it names no directory")
    return value


def _referent_value(
    workset_root: Path, doc: Mapping[str, Any] | None, referent: str, *, key: str,
    standalone: bool | None, chain: tuple[str, ...], early: EarlyScope,
) -> str:
    """The resolved ``@workset.<referent>``: its value in the cascade, else its declared default.

    A referent that is unset takes its manifest default in the modes *key* is read in:
    *standalone*'s, or with ``None`` every mode in which *key* has a declared default.  When
    those arms are not one path the reference is REFUSED, naming the referent: the box mode
    is not known here, and a guess would place a directory.  A present ``<None>`` is refused
    the same way, since it names no directory.
    """
    raw, where = early_repoint(workset_root, doc, referent, early=early)
    if raw is None:
        raise SettingsError(f"'@workset.{referent}' is null in {where}, so it names no directory")
    if isinstance(raw, _Unset):
        raw = _mode_default(referent, key=key, standalone=standalone)
    elif not is_unambiguous_path_value(raw):
        raise SettingsError(
            f"'@workset.{referent}' is set to the bare relative path {raw!r} in {where}"
        )
    return _expand_early(
        workset_root, doc, raw, key=key, standalone=standalone, chain=chain, early=early,
    )


def _declared_default(key: str) -> object:
    """``workset.<key>``'s manifest default: one value, or a mapping of box mode to value."""
    from kanibako.settings.keyspace_manifest import manifest_doc

    return manifest_doc()["keys"][f"workset.{key}"]["default"]


def _mode_default(referent: str, *, key: str, standalone: bool | None) -> str:
    """``workset.<referent>``'s declared default in the modes ``workset.<key>`` is read in."""
    from kanibako.settings.paths import BoxMode

    def arm(dotted: str, mode: str) -> object:
        declared = _declared_default(dotted.removeprefix("workset."))
        return declared.get(mode) if isinstance(declared, Mapping) else declared

    if standalone is None:
        modes = [m.value for m in BoxMode if arm(f"workset.{key}", m.value) is not None]
    else:
        modes = [BoxMode.standalone.value] if standalone else [
            BoxMode.primary.value, BoxMode.named.value,
        ]
    arms = {mode: arm(f"workset.{referent}", mode) for mode in modes}
    values = set(arms.values())
    if len(values) == 1 and None not in values:
        return str(values.pop())
    shown = ", ".join(f"{mode}: {value}" for mode, value in arms.items())
    raise SettingsError(
        f"'@workset.{referent}' is unset, and its default is not one path in the box modes "
        f"this key is read in ({shown}); set workset.{referent}"
    )


def _reader_modes(key: str, *, standalone_reads: bool) -> tuple[bool | None, ...]:
    """The *standalone* values ``workset.<key>``'s readers pass, among the modes reading a file.

    A key declared with one default is read with no mode (``None``).  A per-mode key is read
    as primary/named (``False``) where either arm is declared, and as standalone (``True``)
    where that arm is, when *standalone_reads* says a standalone box reads the file.
    """
    from kanibako.settings.paths import BoxMode

    declared = _declared_default(key)
    if not isinstance(declared, Mapping):
        return (None,)
    modes: list[bool | None] = []
    if any(declared.get(m.value) is not None for m in (BoxMode.primary, BoxMode.named)):
        modes.append(False)
    if standalone_reads and declared.get(BoxMode.standalone.value) is not None:
        modes.append(True)
    return tuple(modes)


def early_key_set_error(
    canonical: str, value: str | None, *, written_file: Path, standalone_reads: bool,
    early_system: EarlySystem | None, std_error: str | None = None,
    workset_name: str | None = None,
) -> str | None:
    """The reader's refusal of *value* at a workset early key, or ``None``; the SET door's twin.

    Runs :func:`resolve_workset_dir_key` itself on the value about to be written to
    *written_file*: a workset's own file, or the system settings file that
    :func:`early_repoint` reads beneath it.  So a value the launch snapshot would resolve
    but this reader cannot (``$AGENT``, an ``@``-ref outside :data:`_USABLE_REFS`) is
    refused, because this reader reads it first.  The resolved path is
    discarded, so the file's directory stands in for the workset root, and the file is the
    first tier a referent is read from.  It resolves once per mode the key's readers pass
    (:func:`_reader_modes`; *standalone_reads*: a standalone box reads *written_file*), so a
    value accepted here resolves for every reader of that file.

    ⚑ THE DOOR OPENS ONE FILE, ONCE: *written_file*.  The system tier beneath it is
    *early_system*, the record the command's ``std`` load already settled, never a re-read.
    *workset_name* is the workset door's scope name.  At the SYSTEM door
    (*standalone_reads*: only the system file is read by a standalone box) the file is read
    by every workset, so each pass takes its reader mode's reserved partition name instead,
    and a missing *early_system* means the ``std`` load failed (*std_error*, its text): the
    record is built from the document this door already opened, which IS the system file,
    with no ``system.*`` paths and *std_error* as the refusal that dropped them.
    """
    if not isinstance(value, str) or not canonical.startswith("workset."):
        return None
    key = canonical.removeprefix("workset.")
    if key not in WORKSET_EARLY_KEYS:
        return None
    if not standalone_reads and (early_system is None or workset_name is None):
        raise ValueError("the workset set door needs the std record and the workset name")
    try:
        doc = load_doc(written_file)
        record = early_system if early_system is not None else EarlySystem(
            tier=early_tier(doc), file=written_file, system_paths={},
            system_refusal=std_error,
        )
        for standalone in _reader_modes(key, standalone_reads=standalone_reads):
            resolve_workset_dir_key(
                written_file.parent, value, "", key=key, where=written_file,
                standalone=standalone, workset_settings=doc,
                early=_door_scope(
                    record, None if standalone_reads else workset_name, standalone=standalone,
                ),
            )
    except (SettingsError, ConfigError) as exc:
        return str(exc)
    return None


def _door_scope(
    early_system: EarlySystem, workset_name: str | None, *, standalone: bool | None,
) -> EarlyScope:
    """The set door's scope for one reader-mode pass: *workset_name*, else (the system door)
    the reserved partition of the pass's mode, standalone's or primary's; primary's also
    stands for a key read with no mode.
    """
    from kanibako.channels.channels import workset_token
    from kanibako.settings.paths import BoxMode

    if workset_name is not None:
        return EarlyScope(early_system, workset_name)
    return EarlyScope(early_system, workset_token(
        BoxMode.standalone if standalone else BoxMode.primary, None,
    ))
