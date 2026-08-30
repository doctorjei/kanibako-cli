"""Eager build-time EXPANSION — resolve the merged snapshot's tokens to terminals.

ONE pure function, :func:`expand`, walks
:mod:`kanibako.settings.settings_merge`'s raw merged
:class:`~kanibako.settings.keystore.KeyStore` snapshot and resolves every
``@``-ref (CONFIG, both bind sides) and host-side ``$VAR`` / ``~`` (ENVIRONMENT)
to terminals — TRANSITIVELY (a fixpoint), with cycle detection. It is PURE, and it
NEVER mutates the input snapshot (S19): it builds a fresh ``KeyStore``.

⚑ A reference resolves to a DECLARED key or it does not resolve at all — this pass
NEVER fabricates a default for a name it cannot find. An absent referent propagates
ABSENCE (§6b: whole-value → the holder key is DROPPED, embedded → ``""``), and
every other unresolvable case is an ERROR that NAMES the key: a cycle, a depth-cap
breach, an unknown ``$VAR``, a ``@pref.*`` ref, or a binding destination that would
resolve to no path.

⚑ ``box_dest`` keeps its ``$XDG``/``~`` RAW (S17): ENVIRONMENT differs host vs box,
so those tokens are DEFERRED to mount time. ``@``-refs (CONFIG) expand BOTH sides.
An expanded ``Bind.box`` may therefore still carry a token — a known, bounded
residue, NOT lazy config re-resolution.

It WRAPS :func:`kanibako.settings.settings_resolve.expand_expr` (the single-expr
scanner) and does not modify it; it adds transitive snapshot lookup, whole-value
3-state propagation and the CONFIG-vs-ENV deferral.

The per-leaf case enumeration, LENIENT mode's contract, the ``pref`` rules, the
resolver split, the out-of-scope boundaries, the authority (design §3/§6a/§6b/§6h,
spec §0/§1/§1A/§2a/§2c/§2h) and the seams S3/S17/S18/S19 are all in
``llm-docs/kanibako/settings/settings_expand.py.md``.
"""

from __future__ import annotations

from typing import overload

from kanibako.settings.kb_store import Bind, BindEntry, StoreValue
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_categories import BARE_RELATIVE_SOURCE_HAZARD
from kanibako.settings.settings_resolve import (
    MAX_REF_DEPTH,
    ResolveCtx,
    SettingsError,
    expand_expr,
    match_ref,
)


class _Absent:
    """Sentinel: a ref resolved to a LEGITIMATELY ABSENT key (§6b propagation).

    Distinct from a *cycle* (which raises) and from a stored ``None`` (present-
    None, a real terminal). Module-private, never stored, never a member of
    :data:`~kanibako.settings.kb_store.StoreValue`.
    """

    _instance: "_Absent | None" = None

    def __new__(cls) -> "_Absent":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "_ABSENT"


#: A whole-value ``@``-ref whose referent (transitively) does not exist resolves
#: to this; the holder key is then DROPPED from the expanded snapshot (§6b).
_ABSENT: _Absent = _Absent()

#: The top-level table holding ``pref.*`` REQUESTS (spec §2h): carried through
#: UNEXPANDED and never ``@``-referenceable. Spelled here rather than imported —
#: a ``settings_prefs`` import would cycle through the settings stack.
_PREF_ROOT = "pref"


class _LenientDefect(Exception):
    """Internal (lenient-mode only) signal: the leaf being expanded is unresolvable.

    Caught by :meth:`_Expander._expand_node` at the OWNING leaf, which records the
    dotted path → *reason* in the error map and omits the leaf. It NEVER escapes
    :func:`expand` (a leaf-local control signal, not a user error) and is never
    raised in strict mode.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_whole_value_ref(value: str) -> str | None:
    """Return the dotted ref name iff *value* IS exactly one whole-value ``@``-ref.

    S18 — the shape is decided by PARSE, never guessed, via the shared
    :func:`~kanibako.settings.settings_resolve.match_ref` grammar. ``"@a.b"`` /
    ``"@{a.b}"`` → ``"a.b"``; anything with a leading, trailing or embedded literal
    → ``None`` (the embedded path, handled by ``expand_expr`` substitution).

    ⚑ THE BRACED FORM MUST LAND HERE, NOT ON THE EMBEDDED PATH. This predicate is
    the ONLY thing that decides the shape, and a braced whole-value ref misrouted
    to the embedded path silently turns a present-``None`` (the §3 "omit this bind"
    terminal) into an empty-string terminal — a real value where the spec means
    absence. See the llm-doc.

    NEVER RAISES — a total predicate; a malformed reference answers ``None`` and
    ``expand_expr`` raises it downstream with unchanged provenance.
    """
    if not value or value[0] != "@":
        return None
    try:
        name, end = match_ref(value, 0)
    except SettingsError:
        return None
    return name if end == len(value) else None


@overload
def expand(snapshot: KeyStore, ctx: ResolveCtx) -> KeyStore: ...
@overload
def expand(
    snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool
) -> KeyStore | tuple[KeyStore, dict[str, str]]: ...


def expand(
    snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool = False
) -> KeyStore | tuple[KeyStore, dict[str, str]]:
    """Expand *snapshot*'s tokens to terminals, returning a FRESH KeyStore (S19).

    *snapshot* is block 2b's raw merged store (refs/vars/``~`` intact). *ctx*
    carries the host-side expansion namespace (``host_home``, ``xdg``,
    ``agent_name``, ``workset_name``) consumed for host-side ``$VAR`` / ``~``.
    Every value is resolved TRANSITIVELY to a fixpoint (§6h), so a multi-hop chain
    collapses to its terminal regardless of dict order. The per-leaf rules for
    each value shape are enumerated in the llm-doc.

    STRICT mode (``collect_errors=False``, the default — the live launch read-path):
    a CYCLE (whole-value or embedded — B7) raises :class:`SettingsError` with the
    chain; this is DISTINCT from a legitimately absent/None referent (propagated,
    not raised). Returns the fresh expanded :class:`KeyStore`.

    LENIENT mode (``collect_errors=True`` — Q9 set-time validation only): nothing
    raises; each DEFECTIVE leaf is RECORDED in an error map keyed by its dotted
    path (path → human reason) and OMITTED, while every clean leaf still resolves.
    Returns ``(snapshot, errors)``.

    The input snapshot is never mutated (S19).
    """
    expander = _Expander(snapshot, ctx, collect_errors=collect_errors)
    expanded = expander.run()
    if collect_errors:
        return expanded, expander.errors
    return expanded


class _Expander:
    """The per-pass expansion state: the source snapshot, ctx, and the fixpoint memo.

    One instance per :func:`expand` call (pure — no cross-call state). The snapshot
    is read-only here (S19); the fresh tree is built in :meth:`run`.
    """

    def __init__(
        self, snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool = False
    ) -> None:
        self._snapshot = snapshot
        self._ctx = ctx
        # Memo: dotted path -> fully-resolved value (or _ABSENT). ⚑ None and
        # _ABSENT are both VALID memo values, so membership is tested with ``in``,
        # never by comparing to a sentinel. A path mid-resolution is not in the
        # memo; the ``chain`` argument is what detects a cycle.
        self._memo: dict[str, StoreValue | _Absent] = {}
        # LENIENT mode (Q9): collect defects instead of raising/silent-drop, keyed
        # by the OWNING leaf's dotted path → human reason.
        self._collect_errors = collect_errors
        self.errors: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Tree walk — build the fresh expanded snapshot                      #
    # ------------------------------------------------------------------ #

    def run(self) -> KeyStore:
        """Walk the source snapshot and build the fresh expanded tree (S19)."""
        return self._expand_node(self._snapshot, path=())

    def _expand_node(self, node: KeyStore, *, path: tuple[str, ...]) -> KeyStore:
        """Build a fresh expanded KeyStore mirroring *node* at *path*.

        A child KeyStore recurses; a leaf goes to :meth:`_expand_leaf`; a
        whole-value ``@``-ref leaf that resolves ABSENT is DROPPED (§6b). Uses the
        UNBOUND ``dict`` protocol (S3) so a key named ``keys`` / ``items`` / ``get``
        cannot shadow it.
        """
        out = KeyStore()
        for key in dict.keys(node):
            child_path = (*path, key)
            value = dict.__getitem__(node, key)
            if not path and key == _PREF_ROOT and isinstance(value, KeyStore):
                # ⚑ The ``pref`` subtree is CARRIED THROUGH VERBATIM, unexpanded
                # (spec §2h: prefs never participate in resolution as derivable
                # keys). Expanding it would resolve every pref value TWICE and
                # would destroy the RAW REQUEST ``--effective`` displays. Guarded
                # on the ROOT path (``not path``) so a category legitimately NAMED
                # ``pref`` deeper in the tree is unaffected.
                from kanibako.settings.settings_merge import _deep_copy_store

                out[key] = _deep_copy_store(value)
                continue
            if isinstance(value, KeyStore):
                out[key] = self._expand_node(value, path=child_path)
                continue
            if self._collect_errors:
                # LENIENT (Q9): a defect anywhere in THIS leaf's transitive chain
                # surfaces here. Record it against the OWNING leaf path and OMIT
                # the leaf; every clean leaf still resolves. STRICT never enters.
                try:
                    out_key = self._expand_dest_key(key, value, chain=child_path)
                    resolved = self._expand_leaf(value, path=child_path)
                except (_LenientDefect, SettingsError) as exc:
                    reason = exc.reason if isinstance(exc, _LenientDefect) else str(exc)
                    self.errors[".".join(child_path)] = reason
                    continue
            else:
                out_key = self._expand_dest_key(key, value, chain=child_path)
                resolved = self._expand_leaf(value, path=child_path)
            if resolved is _ABSENT:
                continue  # whole-value ref to an absent key → drop this key (§6b).
            if isinstance(value, BindEntry) and dict.__contains__(out, out_key):
                # ⚑ Two DIFFERENT stored dests expanded to ONE destination (files
                # store UNRESOLVED — design §2b-CAVEAT). Installing the second
                # would silently DELETE the first, a data loss no downstream check
                # can see. Raised even in LENIENT mode: the fault is the PAIR, so
                # there is no single owning leaf to attribute it to.
                raise SettingsError(
                    f"Two bindings under {'.'.join(path) or '<root>'} resolve to "
                    f"the same destination {out_key!r}; the second entry "
                    f"({key!r}) would silently replace the first."
                )
            out[out_key] = resolved
        return out

    def _expand_dest_key(
        self, key: str, value: StoreValue, *, chain: tuple[str, ...]
    ) -> str:
        """The OUTPUT key for *value* — identity, EXCEPT under a dest-keyed arm.

        A :class:`BindEntry` lives in a DEST-KEYED bindings arm (R-5/R-6) where the
        destination has become the mapping KEY, so the key is the box-side path
        EXPRESSION and expands exactly as ``Bind.box`` does (``space="defer"``,
        S17). Every other value shape carries its key through verbatim.

        ⚑ Discrimination is by TYPE (``isinstance(value, BindEntry)``), never by
        the key's spelling or the value's arity — a legacy :class:`Bind` and a
        :class:`BindEntry` are both 2-element-legal with opposite meanings.
        """
        if not isinstance(value, BindEntry):
            return key
        dest = self._expand_str(key, space="defer", chain=(".".join(chain),))
        if dest is _ABSENT or dest is None:
            state = "absent" if dest is _ABSENT else "present-None"
            raise SettingsError(
                f"Binding destination is a whole-value @-reference to an "
                f"{state} config key ({key!r}); a box destination cannot "
                f"resolve to no path."
            )
        assert isinstance(dest, str)
        return dest

    def _expand_leaf(
        self, value: StoreValue, *, path: tuple[str, ...]
    ) -> StoreValue | _Absent:
        """Expand a single non-KeyStore leaf (scalar / Bind / BindEntry / list / None).

        Returns the expanded terminal, ``None`` (present-None inherited from a
        whole-value ref), or :data:`_ABSENT` (the caller DROPS the key). The
        ``chain`` starts at this leaf's own dotted path so a self-referential
        whole-value ``@`` is a cycle, not an infinite recurse.
        """
        chain = (".".join(path),)
        if isinstance(value, Bind):
            return self._expand_bind(value, chain=chain)
        if isinstance(value, BindEntry):
            return self._expand_bind_entry(value, chain=chain)
        if isinstance(value, str):
            return self._expand_str(value, space="host", chain=chain)
        # No token to expand. (A present-None leaf is a terminal, not _ABSENT.)
        return value

    def _refuse_relative_host_src(
        self, raw: str, expanded: str, *, chain: tuple[str, ...]
    ) -> None:
        """Refuse a host source that EXPANDED to a bare relative path (spec §2a, [R147]).

        ⚑ THE PARSE CANNOT COVER THIS ONE, which is why the guard is here as well.
        ``settings_assemble._declared_source`` refuses a source SPELLED relative, and
        that is what ``settings_launch`` relies on when it uses ``host_src`` AS-IS —
        but a source spelled ``@box.canon/handbook`` is self-resolving at the parse and
        only becomes relative HERE, when the path key it dereferences turns out to hold
        a bare relative.  Measured before the guard existed: it reached the mount as
        ``host_src='relcanon/handbook'``.
        🛑 The refusal names the KEY-BEARING EXPRESSION, not just the result — the fix
        is to the path key, and the expanded string no longer says which one that was.
        """
        if not expanded or expanded[0] == "/":
            return
        became = "" if raw == expanded else f", which resolved to {expanded!r}"
        raise SettingsError(
            f"{chain[0]} declares the host source {raw!r}{became} — a BARE RELATIVE "
            f"path. A stored source must resolve on its own (spec §2a): "
            f"{BARE_RELATIVE_SOURCE_HAZARD}. Set the path key it dereferences to an "
            f"absolute path, '~/...', '$XDG_*/...' or an '@'-ref."
        )

    def _expand_bind(self, bind: Bind, *, chain: tuple[str, ...]) -> StoreValue | _Absent:
        """Expand a :class:`Bind`: ``host_src`` fully host-side; ``box_dest``
        ``@``-refs only (``$XDG``/``~`` left RAW, deferred box-side — S17).

        A whole-value ``host_src`` ref that resolves absent/None gives the WHOLE
        Bind that 3-state — the binding cannot point anywhere. ``opts`` is carried
        verbatim; it never holds tokens.
        """
        host = self._expand_str(bind.host, space="host", chain=chain)
        if host is _ABSENT or host is None:
            # Whole-value host ref absent/None → the bind inherits that 3-state.
            return host
        box = self._expand_str(bind.box, space="defer", chain=chain)
        # ⚑ Reads as dead code and is not: a box_dest is a path EXPRESSION, not a
        # key whose absence deletes the bind, and the spec has NO whole-value
        # box_dest — so this arm is unreachable on spec forms. Raise loudly rather
        # than silently emit an empty dest, which is a mount foot-gun.
        if box is _ABSENT or box is None:
            state = "absent" if box is _ABSENT else "present-None"
            raise SettingsError(
                f"Bind box_dest is a whole-value @-reference to an "
                f"{state} config key ({bind.box!r}); a box destination cannot "
                f"resolve to no path."
            )
        assert isinstance(host, str)
        assert isinstance(box, str)
        self._refuse_relative_host_src(bind.host, host, chain=chain)
        return Bind(host, box, bind.opts)

    def _expand_bind_entry(
        self, entry: BindEntry, *, chain: tuple[str, ...]
    ) -> StoreValue | _Absent:
        """Expand a :class:`BindEntry`: ``src`` fully host-side; ``opts`` verbatim.

        The dest-keyed counterpart of :meth:`_expand_bind`. It expands ONE half,
        because the other half — the destination — is the mapping KEY and is
        expanded by :meth:`_expand_dest_key` on the node walk (R-5/R-6). The
        3-state rule is unchanged from the name-keyed shape.
        """
        src = self._expand_str(entry.src, space="host", chain=chain)
        if src is _ABSENT or src is None:
            # Whole-value src ref absent/None → the entry inherits that 3-state.
            return src
        assert isinstance(src, str)
        self._refuse_relative_host_src(entry.src, src, chain=chain)
        return BindEntry(src, entry.opts)

    def _expand_str(
        self,
        value: str,
        *,
        space: str,
        chain: tuple[str, ...],
    ) -> StoreValue | _Absent:
        """Expand a single string leaf in *space* (``"host"`` or ``"defer"``).

        WHOLE-VALUE ``@``-ref (S18) → INHERIT the referent's full 3-state
        (``_ABSENT`` / ``None`` / the terminal). EMBEDDED token or plain literal →
        ``expand_expr`` substitution (absent/None token → empty string).

        *space*: ``"host"`` expands ``~``/``$VAR`` host-side; ``"defer"`` leaves
        them RAW for the box side (S17). ``@``-refs expand in BOTH spaces.
        """
        ref_name = _is_whole_value_ref(value)
        if ref_name is not None:
            return self._resolve_ref(ref_name, chain=(*chain, ref_name))
        return self._expand_embedded(value, space=space, chain=chain)

    # ------------------------------------------------------------------ #
    # Reference resolution — the transitive fixpoint + cycle guard       #
    # ------------------------------------------------------------------ #

    def _resolve_ref(
        self, dotted: str, *, chain: tuple[str, ...]
    ) -> StoreValue | _Absent:
        """Fully resolve the value at snapshot path *dotted*, transitively (§6h).

        Reads the RAW value at *dotted*, then expands THAT value (recursing) so the
        result is a terminal. MEMOIZED by dotted path — the fixpoint. 3-state: an
        absent path → :data:`_ABSENT`; a present-None leaf → ``None``; else the
        expanded value. The depth cap (``MAX_REF_DEPTH``) bounds pathological
        non-cyclic chains.

        *chain* is the in-progress ref trail, ending in *dotted*: already checked
        and appended by the caller, mirroring ``expand_expr``'s contract.
        """
        # CYCLE GUARD (B7 — whole-value AND embedded paths): a PRIOR occurrence of
        # *dotted* means we re-entered a ref still in progress. ⚑ Checked BEFORE
        # the memo so a cycle can never be masked by a half-built memo entry.
        if dotted in chain[:-1]:
            cycle = " -> ".join(chain)
            if self._collect_errors:
                # LENIENT (Q9): RECORD, do not raise. The guard still fires here,
                # so the pass TERMINATES rather than re-entering the ref.
                raise _LenientDefect(f"cyclic @-reference: {cycle}")
            raise SettingsError(f"Cyclic @-reference: {cycle}")
        if dotted in self._memo:
            return self._memo[dotted]
        if len(chain) > MAX_REF_DEPTH:
            if self._collect_errors:
                raise _LenientDefect(
                    f"@-reference depth cap ({MAX_REF_DEPTH}) exceeded resolving "
                    f"'{dotted}'"
                )
            raise SettingsError(
                f"@-reference depth cap ({MAX_REF_DEPTH}) exceeded resolving "
                f"'{dotted}'."
            )
        raw = self._lookup_raw(dotted)
        if raw is _ABSENT:
            if self._collect_errors:
                # LENIENT (Q9): a DANGLING ref is a set-time defect to record, NOT
                # the strict §6b silent drop. Raised so the OWNING leaf gets it.
                raise _LenientDefect(
                    f"dangling @-reference '@{dotted}' "
                    f"(no such config key in the keyspace)"
                )
            self._memo[dotted] = _ABSENT
            return _ABSENT
        # Resolve the referent's value AS A LEAF, with the cycle chain threaded so
        # a ref back into this path (directly or transitively) is caught.
        # ⚑ A nested KeyStore referent is degenerate, but it MUST route through
        # ``_expand_node``: a bare ``resolved = raw`` would ALIAS the input tree
        # (S19) and would leave the subtree's own tokens unexpanded.
        if isinstance(raw, KeyStore):
            resolved: StoreValue | _Absent = self._expand_node(
                raw, path=tuple(dotted.split("."))
            )
        elif isinstance(raw, Bind):
            resolved = self._expand_bind(raw, chain=chain)
        elif isinstance(raw, BindEntry):
            # The entry alone: its destination is the KEY, unreachable by value ref.
            resolved = self._expand_bind_entry(raw, chain=chain)
        elif isinstance(raw, str):
            resolved = self._expand_str(raw, space="host", chain=chain)
        else:
            resolved = raw  # int / float / bool / None / list — verbatim terminal.
        self._memo[dotted] = resolved
        return resolved

    def _lookup_raw(self, dotted: str) -> StoreValue | _Absent:
        """Read the RAW (unexpanded) value at snapshot path *dotted*, 3-state.

        Resolver SPLIT (spec §1A / JC-2): a ``config.*`` ref routes to the Layer-1
        CONFIG-key FOUNDATION (``ctx.config``), NOT the settings snapshot — config
        is a foundation, not a cascade level. Every other prefix walks the merged
        snapshot.

        Walks the dotted segments with the UNBOUND ``dict.get(node, seg, _ABSENT)``
        probe (S3): any missing segment, or a non-KeyStore node reached before the
        last segment, yields :data:`_ABSENT` (the path does not exist). The final
        segment's value is returned verbatim (a present-``None`` leaf → ``None``).
        """
        if dotted == _PREF_ROOT or dotted.startswith(f"{_PREF_ROOT}."):
            # ⚑ A ``@pref.…`` reference is REFUSED, not resolved (spec §2h).
            # RAISED rather than answered ``_ABSENT``: absent would silently DROP
            # the referring key (§6b), the failure class this phase exists to
            # eliminate. ``_expand_node`` catches it into the lenient error map.
            raise SettingsError(
                f"'@{dotted}' is not resolvable: a pref is a REQUEST, not a "
                f"value (spec §2h). Reference the TARGET key instead."
            )
        if dotted.startswith("config."):
            # @config.* → the Layer-1 foundation (prefix-driven; single-route).
            return self._ctx.config.get(dotted, _ABSENT)
        node: object = self._snapshot
        segments = dotted.split(".")
        for seg in segments[:-1]:
            if not isinstance(node, KeyStore):
                return _ABSENT
            node = dict.get(node, seg, _ABSENT)
            if node is _ABSENT:
                return _ABSENT
        if not isinstance(node, KeyStore):
            return _ABSENT
        got = dict.get(node, segments[-1], _ABSENT)
        return got

    # ------------------------------------------------------------------ #
    # Embedded-token substitution — wraps expand_expr                    #
    # ------------------------------------------------------------------ #

    def _expand_embedded(
        self, value: str, *, space: str, chain: tuple[str, ...]
    ) -> str:
        """Substitute embedded tokens in *value* via ``expand_expr`` (§6b).

        An ``@``-ref token resolves through :meth:`_lookup_str` (absent/None →
        ``""``); ``~``/``$VAR`` expand host-side for ``space="host"`` and are left
        RAW (``defer_env=True``) for ``space="defer"`` (S17). A cycle reached
        through an embedded token still raises (B7). ONE scanner serves both
        spaces — no fork; the deferral is the engine's additive ``defer_env`` flag.
        """
        return expand_expr(
            value,
            space="host",
            ctx=self._ctx,
            lookup=lambda ref, ch: self._lookup_str(ref, ch),
            chain=chain,
            defer_env=(space == "defer"),
        )

    def _lookup_str(self, dotted: str, chain: tuple[str, ...]) -> str:
        """``expand_expr`` lookup: resolve *dotted* and coerce to a SUBSTITUTION
        string (the embedded-token rule, §6b).

        Reuses the transitive resolver, so embedded refs are fixpoint- and
        cycle-guarded too (B7). STRICT: an absent or present-None referent → ``""``,
        an empty substitution that never deletes the host key. *chain* is
        ``expand_expr``'s already-extended trail.

        LENIENT (Q9): an ABSENT referent never reaches that coercion —
        ``_resolve_ref`` raises ``_LenientDefect`` first. A present-None referent is
        still a legitimate ``""``. Only the absent case diverges.
        """
        resolved = self._resolve_ref(dotted, chain=chain)
        if resolved is _ABSENT or resolved is None:
            return ""
        if isinstance(resolved, Bind):
            # Degenerate but total: a Bind has no single string form → its host.
            return resolved.host
        if isinstance(resolved, BindEntry):
            # Same, dest-keyed: the destination is the key, not part of the value.
            return resolved.src
        return str(resolved)
