"""The NO-SNAPSHOT resolver for the workset dir keys — ONE route, ONE grammar.

``workset.{workspaces,boxes,logs,channelroot,registry,canon,template}`` are read on the DETECTION /
paths side, which runs BEFORE the per-launch KeyStore snapshot exists — it is the pass
that FINDS the workset the snapshot will later be built for.  So it cannot call
:func:`kanibako.settings.settings_expand.expand`, which needs that snapshot.

⚑⚑ IT STILL MUST NOT GROW A SECOND GRAMMAR.  Files store entries UNRESOLVED (spec
``:214`` — ``@``-refs and ``$XDG``/``~`` verbatim), so every one of these values may
carry a token, and the spec's own per-mode DEFAULT for all seven is
``@meta.workset.path/<leaf>``.  A resolver that merely ``expanduser()``-ed the string
turned that documented default into a literal directory named ``@meta.workset.path``
— silently relocating the box store, while the launch snapshot resolved the same key
correctly.  Two carriers, two answers.

⚑ What this module does instead: it is a THIRD CALLER of the single expression scanner
:func:`~kanibako.settings.settings_resolve.expand_expr` (seam S25), with a lookup
NARROWED to the one reference that is knowable without a snapshot —
:data:`WORKSET_PATH_REF`, whose value is the workset root the caller already holds.
``~`` and ``$XDG_*`` expand host-side exactly as they do at launch.  Every other
reference is REFUSED BY NAME.  A refusal that names the key and the token is a correct
answer to "this cannot be resolved yet"; a directory called ``@config.registry`` is not.
"""

from __future__ import annotations

from pathlib import Path

from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError, expand_expr

#: The ONE ``@``-ref a workset dir key can resolve before a snapshot exists: the
#: workset root, which every caller of :func:`resolve_workset_dir_key` already has in
#: hand.  It is also the anchor of all five keys' spec-declared defaults, so the
#: documented value resolves here without the snapshot the rest of the keyspace needs.
WORKSET_PATH_REF = "meta.workset.path"


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
) -> Path:
    """Resolve the ``workset.<key>`` *repoint* (or its ``<root>/<default_leaf>`` default).

    *repoint* is the RAW value as stored: it may carry ``@``-refs, ``$XDG_*`` or ``~``.
    An unset repoint takes the default leaf under *workset_root*; a resolved value that
    is still RELATIVE anchors under *workset_root* too.  Raises
    :class:`~kanibako.settings.settings_resolve.SettingsError`, naming the key, the file
    and the token, when the value cannot be resolved without the launch snapshot.
    """
    if not repoint:
        return workset_root / default_leaf

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        del chain  # No transitive resolution here: the one referent is a terminal.
        if ref == WORKSET_PATH_REF:
            return str(workset_root)
        raise SettingsError(
            f"'@{ref}' cannot be resolved here: this key is read before the launch "
            f"snapshot exists, so '@{WORKSET_PATH_REF}' (this workset's root) is the "
            f"only reference available to it"
        )

    try:
        expanded = expand_expr(repoint, space="host", ctx=_host_ctx(), lookup=lookup)
    except SettingsError as exc:
        raise SettingsError(
            f"workset.{key} is set to {repoint!r} in "
            f"{workset_root / WORKSET_META_FILE}, which cannot be resolved: {exc}. "
            f"Use an absolute path, a path relative to the workset root, '~', "
            f"'$XDG_*', or '@{WORKSET_PATH_REF}'; a LITERAL '$', '~' or '@' in a "
            f"directory name must be backslash-escaped."
        ) from exc

    resolved = Path(expanded)
    return resolved if resolved.is_absolute() else workset_root / resolved
