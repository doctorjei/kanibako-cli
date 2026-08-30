"""The NO-SNAPSHOT resolver for the workset dir keys — ONE route, ONE grammar.

``workset.{workspaces,boxes,logs,channelroot,registry,canon,template,vault_ro,vault_rw}`` are read
on the DETECTION / paths side, which runs BEFORE the per-launch KeyStore snapshot exists — it is the pass
that FINDS the workset the snapshot will later be built for.  So it cannot call
:func:`kanibako.settings.settings_expand.expand`, which needs that snapshot.

⚑⚑ IT STILL MUST NOT GROW A SECOND GRAMMAR.  Files store entries UNRESOLVED (spec
``:214`` — ``@``-refs and ``$XDG``/``~`` verbatim), so every one of these values may
carry a token, and the spec's own per-mode DEFAULT for all nine is
``@meta.workset.path/<leaf>`` (the two vault arms take a TWO-SEGMENT leaf,
``vault/ro`` and ``vault/rw``).  A resolver that merely ``expanduser()``-ed the string
turned that documented default into a literal directory named ``@meta.workset.path``
— silently relocating the box store, while the launch snapshot resolved the same key
correctly.  Two carriers, two answers.

⚑ What this module does instead: it is a THIRD CALLER of the single expression scanner
:func:`~kanibako.settings.settings_resolve.expand_expr` (seam S25), with a lookup
NARROWED to the references that are knowable without a snapshot — :data:`WORKSET_PATH_REF`,
whose value is the workset root the caller already holds, plus whatever the CALLER can
itself answer and hands in as ``extra_refs``.  ``~`` and ``$XDG_*`` expand host-side
exactly as they do at launch.  Every reference the caller cannot itself answer is
REFUSED BY NAME.  A refusal that names the key and the token is a correct answer to
"this cannot be resolved yet"; a directory called ``@config.registry`` is not.

🛑 ``extra_refs`` IS NOT A GENERALITY HATCH, and the reason is the shape of the one
caller that uses it.  A ref is admissible here only when the caller ALREADY HOLDS its
value before the snapshot exists — not when it merely knows the formula.
``resolve_workset_logs(..., standalone=True)`` qualifies: a lone box's
``meta.box.path`` is ``@workset.boxes`` with NO name leaf (the launch floor's own
standalone formula), and ``workset.boxes`` resolves through this very route, so the
caller resolves it once and passes the ANSWER.  The same ref in primary or named mode
does NOT qualify — there ``meta.box.path`` is ``@workset.boxes/@meta.box.name``, and
``meta.box.name`` is construct-time.  Admitting it there would not refuse; it would
yield a trailing-separator box root — a syntactically perfect ``/mybox`` that no shape
check rejects and that then holds data.  Widen this only with a value in hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from kanibako.settings.agent_config import (
    ambiguous_path_value_error,
    is_unambiguous_path_value,
)
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
    extra_refs: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the ``workset.<key>`` *repoint* (or its ``<root>/<default_leaf>`` default).

    *repoint* is the RAW value as stored: it may carry ``@``-refs, ``$XDG_*`` or ``~``.
    *extra_refs* maps ref name -> ALREADY-RESOLVED value for refs the caller can answer
    itself; see the module docstring for why it is not a general widening.
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
                where=str(workset_root / WORKSET_META_FILE),
            )
        )

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        del chain  # No transitive resolution here: every referent is a terminal.
        if ref == WORKSET_PATH_REF:
            return str(workset_root)
        if extra_refs is not None and ref in extra_refs:
            return extra_refs[ref]
        available = [f"'@{WORKSET_PATH_REF}' (this workset's root)"]
        available += [f"'@{name}'" for name in (extra_refs or ())]
        detail = (
            f"{available[0]} is the only reference available to it"
            if len(available) == 1
            else f"only {', '.join(available)} are available to it"
        )
        raise SettingsError(
            f"'@{ref}' cannot be resolved here: this key is read before the launch "
            f"snapshot exists, so {detail}"
        )

    try:
        expanded = expand_expr(repoint, space="host", ctx=_host_ctx(), lookup=lookup)
    except SettingsError as exc:
        usable = ", ".join(f"'@{name}'" for name in (WORKSET_PATH_REF, *(extra_refs or ())))
        raise SettingsError(
            f"workset.{key} is set to {repoint!r} in "
            f"{workset_root / WORKSET_META_FILE}, which cannot be resolved: {exc}. "
            f"Use an absolute path, '~', '$XDG_*', or {usable}; a "
            f"LITERAL '$', '~' or '@' in a directory name must be "
            f"backslash-escaped."
        ) from exc

    return Path(expanded)
