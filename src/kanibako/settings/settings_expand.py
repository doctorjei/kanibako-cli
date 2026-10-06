"""Eager build-time EXPANSION — resolve the merged snapshot's tokens to terminals.

ONE pure function, :func:`expand`, walks
:mod:`kanibako.settings.settings_merge`'s raw merged
:class:`~kanibako.settings.keystore.KeyStore` snapshot and resolves every
``@``-ref (CONFIG, both bind sides) and host-side ``$VAR`` / ``~`` (ENVIRONMENT)
to terminals — TRANSITIVELY (a fixpoint), with cycle detection. It is PURE, and it
NEVER mutates the input snapshot (S19): it builds a fresh ``KeyStore``.

⚑ A reference resolves to a DECLARED key or it does not resolve at all — this pass
NEVER fabricates a default for a name it cannot find. An absent referent that is a
declared key propagates ABSENCE (§6b: whole-value → the holder key is DROPPED,
embedded → ``""``). A present-``None`` referent makes the WHOLE value ``None``,
whole-value or embedded alike (spec §0, [R186]). Every other unresolvable case is an
ERROR that NAMES the key: a ref that names no declared key, a cycle, a depth-cap
breach, an unknown ``$VAR``, a ``@pref.*`` ref, or a binding destination that would
resolve to no path.

⚑ ABSENCE HAS A SECOND SOURCE, and it is not a failure: a PASSTHROUGH variable
(``$COLORTERM``) whose host signal is unset answers absence too, and a whole-value
one DROPS its key by the same §6b rule. Nothing is delivered empty on that path.
🛑 THE DROP IS NOT CONFINED TO ``env``, and the reach is worth stating plainly: a
whole-value passthrough written as a BINDING's host source drops that binding, with
no message — exactly as an absent whole-value ``@``-referent in the same position
always has. One rule, two spellings. A passthrough is a terminal CLAIM about the
host's display, never a path, so naming one as a source is a mis-write the expansion
cannot tell from a deliberate §6b drop.

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

from typing import Callable, cast, overload

from kanibako.settings.kb_store import Bind, BindEntry, StoreValue, __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_categories import BARE_RELATIVE_SOURCE_HAZARD
from kanibako.settings.settings_keyspace import KeyClass, entry_label
from kanibako.settings.settings_keyspace_probe import keyspace_verdict
from kanibako.settings.settings_resolve import (
    MAX_REF_DEPTH,
    ResolveCtx,
    SettingsError,
    deferred_literal_expr,
    expand_expr,
    is_verbatim_text,
    match_braced,
    match_ref,
    match_var,
    resolve_var,
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


def _absent_reason(dotted: str) -> str | None:
    """Why *dotted* is not in the snapshot, or ``None`` when it is a DECLARED key.

    ⚑ A set-time command judges a value against ITS OWN cascade, so a declared key living
    in a scope that cascade does not reach is absent BY CONSTRUCTION — not a keyspace
    breach. Declared is the set door's verdict
    (:func:`~kanibako.settings.config_keys.scope_key_reason`, agent names discovered).
    """
    from kanibako.settings.config_keys import scope_key_reason

    return scope_key_reason(dotted)


_NOT_IN_CASCADE = "declared in the keyspace, but not in this command's cascade"


def is_cascade_blindness(reason: str) -> bool:
    """Whether a LENIENT error *reason* is cascade blindness alone, with no defect behind it."""
    return reason.endswith(f"({_NOT_IN_CASCADE})")


#: The top-level table holding ``pref.*`` REQUESTS (spec §2h): carried through
#: UNEXPANDED and never ``@``-referenceable. Spelled here rather than imported —
#: a ``settings_prefs`` import would cycle through the settings stack.
_PREF_ROOT = "pref"

#: The ``seeded`` CATEGORY token (spec §2a): a seeded entry is a LAYER, and "any layer
#: whose source/dest is ``<None>`` is SKIPPED" — so a present-``None`` DESTINATION
#: skips the layer instead of raising (:meth:`_Expander._expand_dest_key`).  Its
#: source side needs no token: a ``None`` source is a ``None`` entry in every category,
#: and the collapse skips it.  Spelled here for the same reason as
#: ``_PREF_ROOT``: it names a TOKEN of the tree this pass walks, matched by position.
_SEEDED = "seeded"


class _LenientDefect(Exception):
    """Internal (lenient-mode only) signal: the leaf being expanded is unresolvable.

    Caught by :meth:`_Expander._expand_node` at the OWNING leaf, which records the
    dotted path → *reason* in the error map and omits the leaf. It NEVER escapes
    :func:`expand` (a leaf-local control signal, not a user error) and is never
    raised in strict mode.
    """

    def __init__(self, reason: str, *, blind: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.blind = blind  # a DECLARED referent this cascade cannot see


class _ExpandedShapeError(SettingsError):
    """A refusal of what a value EXPANDED to, not of a reference in it.

    After an absent referent substitutes ``""`` it is a consequence of that absence, so
    :meth:`_Expander._defect_past_blindness` does not report it as a defect of its own.
    """


def _is_whole_value_ref(value: str) -> str | None:
    """Return the dotted ref name iff *value* IS exactly one whole-value ``@``-ref.

    S18 — the shape is decided by PARSE, never guessed, via the shared
    :func:`~kanibako.settings.settings_resolve.match_ref` grammar. ``"@a.b"`` /
    ``"@{a.b}"`` / ``"{a.b}"`` → ``"a.b"``; anything with a leading, trailing or embedded literal
    → ``None`` (the embedded path, handled by ``expand_expr`` substitution).

    ⚑ THE BRACED FORM MUST LAND HERE, NOT ON THE EMBEDDED PATH. This predicate is
    the ONLY thing that decides the shape, and a braced whole-value ref misrouted
    to the embedded path silently turns an ABSENT referent (the §6b "drop this key"
    signal) into an empty-string terminal — a real value where the spec means
    absence. See the llm-doc.

    NEVER RAISES — a total predicate; a malformed reference answers ``None`` and
    ``expand_expr`` raises it downstream with unchanged provenance.
    """
    if value[:1] == "{":
        return _whole_braced(value, "ref")
    if not value or value[0] != "@":
        return None
    try:
        name, end = match_ref(value, 0)
    except SettingsError:
        return None
    return name if end == len(value) else None


def _is_whole_value_var(value: str) -> str | None:
    """Return the variable NAME iff *value* IS exactly one whole-value ``$VAR``.

    The ``$`` twin of :func:`_is_whole_value_ref`, and it exists for the SAME reason:
    the two paths differ in what they can SAY. An embedded token is string
    substitution and can only ever produce a string, while a whole-value expression
    inherits a THREE-state — and one variable needs the third. ``$COLORTERM`` is a
    PASSTHROUGH of a host signal with no substitute value (spec, ``box.env.COLORTERM``),
    so "the host set none" must reach the walk as absence and DROP the key, never as
    an empty string a reader would take for a capability claim.

    Uses ``match_var``, the SHARED grammar, so ``$X``, ``${X}`` and ``{$X}`` all qualify.
    ``"\\$X"`` / ``"a$X"`` / ``"$X/y"`` / ``"$X "`` → ``None`` (embedded).

    **It NEVER RAISES — a total predicate**, like its ``@`` twin: a malformed reference
    (``"$"``, ``"${X"``) answers ``None`` and falls through to ``expand_expr``, which
    raises it with the same message from the same place it always has.
    """
    if value[:1] == "{":
        return _whole_braced(value, "var")
    if not value or value[0] != "$":
        return None
    try:
        name, end = match_var(value, 0)
    except SettingsError:
        return None
    return name if end == len(value) else None


def _whole_braced(value: str, kind: str) -> str | None:
    """The NAME iff *value* is exactly one braced reference of *kind* (``{a.b}`` / ``{$X}``)."""
    braced = match_braced(value, 0)
    if braced is None or braced[0] != kind or braced[2] != len(value):
        return None
    return braced[1]


#: The E2 side table :func:`expand` fills on request: a bind ENTRY's path (its segments,
#: the raw destination last) → the ``@``-refs in its source that resolved to a present
#: ``None`` and made the entry ``None`` (spec §0).
NullSources = dict[tuple[str, ...], tuple[str, ...]]
#: Each expanded leaf's path → every key its value read, references followed through.
RefsRead = dict[tuple[str, ...], frozenset[str]]
#: A bind entry's arm path plus its STORED destination → the arm key it was filed under.
DestKeys = dict[tuple[str, ...], str]
#: A key the snapshot holds only after expansion → its value, read through the resolver
#: it is handed; ``__MISSING__`` for a key it does not derive.
Derive = Callable[[str, Callable[[str], object]], object]


@overload
def expand(
    snapshot: KeyStore, ctx: ResolveCtx, *, null_sources: NullSources | None = None,
    refs_read: RefsRead | None = None, dest_keys: DestKeys | None = None,
    derive: Derive | None = None,
) -> KeyStore: ...
@overload
def expand(
    snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool,
    null_sources: NullSources | None = None, refs_read: RefsRead | None = None,
    dest_keys: DestKeys | None = None, derive: Derive | None = None,
) -> KeyStore | tuple[KeyStore, dict[str, str]]: ...


def expand(
    snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool = False,
    null_sources: NullSources | None = None, refs_read: RefsRead | None = None,
    dest_keys: DestKeys | None = None, derive: Derive | None = None,
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

    *null_sources*, when given, is filled with the side table
    (:data:`NullSources`): which bind entries came out ``None`` and the refs that made
    them so.  ``settings_launch`` names those keys in the [R185] warning.

    *dest_keys*, when given, is filled with :data:`DestKeys`: the key each bind entry
    was filed under, so a reader holding the stored destination finds the entry
    without expanding it a second time.

    *derive* answers a reference to a key the caller materializes after this pass
    (:data:`Derive`); it is asked only when the snapshot does not hold the key.

    The input snapshot is never mutated (S19).
    """
    expander = _Expander(snapshot, ctx, collect_errors=collect_errors, derive=derive)
    expanded = expander.run()
    if null_sources is not None:
        null_sources.update(expander.null_sources)
    if refs_read is not None:
        refs_read.update(expander.refs_read)
    if dest_keys is not None:
        dest_keys.update(expander.dest_keys)
    if collect_errors:
        return expanded, expander.errors
    return expanded


def _leaf_label(path: tuple[str, ...]) -> str:
    """How a message names the leaf at *path*: a dest-keyed entry is an index."""
    if len(path) < 2:
        return ".".join(path)
    return entry_label(".".join(path[:-1]), path[-1])


class _Expander:
    """The per-pass expansion state: the source snapshot, ctx, and the fixpoint memo.

    One instance per :func:`expand` call (pure — no cross-call state). The snapshot
    is read-only here (S19); the fresh tree is built in :meth:`run`.
    """

    def __init__(
        self, snapshot: KeyStore, ctx: ResolveCtx, *, collect_errors: bool = False,
        derive: Derive | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._ctx = ctx
        self._derive = derive
        # Memo: dotted path -> fully-resolved value (or _ABSENT). ⚑ None and
        # _ABSENT are both VALID memo values, so membership is tested with ``in``,
        # never by comparing to a sentinel. A path mid-resolution is not in the
        # memo; the ``chain`` argument is what detects a cycle.
        self._memo: dict[str, StoreValue | _Absent] = {}
        # LENIENT mode (Q9): collect defects instead of raising/silent-drop, keyed
        # by the OWNING leaf's dotted path → human reason.
        self._collect_errors = collect_errors
        self._blind_absent = False
        self.errors: dict[str, str] = {}
        # E2: a bind entry made ``None`` by its source → the refs that did it.
        self.null_sources: NullSources = {}
        self.refs_read: RefsRead = {}
        self.dest_keys: DestKeys = {}
        self._deps: dict[str, frozenset[str]] = {}
        self._reading: list[set[str]] = []
        # The message spelling of each leaf being expanded, innermost last.
        self._leaf_labels: list[str] = []

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
        seed_map = bool(path) and path[-1] == _SEEDED
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
            self._reading.append(set())
            self._leaf_labels.append(_leaf_label(child_path))
            try:
                if self._collect_errors:
                    # LENIENT (Q9): a defect anywhere in THIS leaf's transitive chain
                    # surfaces here. Record it against the OWNING leaf path and OMIT
                    # the leaf; every clean leaf still resolves. STRICT never enters.
                    try:
                        out_key = self._expand_dest_key(
                            key, value, chain=child_path, seed=seed_map,
                        )
                        if out_key is None:
                            continue  # a seeded layer with a <None> dest is SKIPPED (§2a).
                        resolved = self._expand_leaf(value, path=child_path)
                    except (_LenientDefect, SettingsError) as exc:
                        reason = exc.reason if isinstance(exc, _LenientDefect) else str(exc)
                        if isinstance(exc, _LenientDefect) and exc.blind:
                            reason = self._defect_past_blindness(
                                key, value, child_path, seed=seed_map,
                            ) or reason
                        self.errors[".".join(child_path)] = reason
                        continue
                else:
                    out_key = self._expand_dest_key(
                        key, value, chain=child_path, seed=seed_map,
                    )
                    if out_key is None:
                        continue  # a seeded layer with a <None> dest is SKIPPED (§2a).
                    resolved = self._expand_leaf(value, path=child_path)
            finally:
                read = self._reading.pop()
                self._leaf_labels.pop()
            if read:
                self.refs_read[(*path, out_key)] = frozenset(read)
            if isinstance(value, BindEntry):
                self.dest_keys[child_path] = out_key
            if resolved is _ABSENT:
                continue  # whole-value ref to an absent key → drop this key (§6b).
            if isinstance(value, BindEntry) and dict.__contains__(out, out_key):
                # ⚑ Two DIFFERENT stored dests expanded to ONE destination (files
                # store UNRESOLVED — design §2b-CAVEAT). Installing the second
                # would silently DELETE the first, a data loss no downstream check
                # can see. Raised even in LENIENT mode: the fault is the PAIR, so
                # there is no single owning leaf to attribute it to.
                raise _ExpandedShapeError(
                    f"Two bindings under {'.'.join(path) or '<root>'} resolve to "
                    f"the same destination {out_key!r}; the second entry "
                    f"({key!r}) would silently replace the first."
                )
            out[out_key] = resolved
        return out

    def _defect_past_blindness(
        self, key: str, value: StoreValue, path: tuple[str, ...], *, seed: bool,
    ) -> str | None:
        """The reason of a defect in this leaf's chain that is NOT cascade blindness.

        Spec §2a: a refusal names the broken upstream dependency. A declared referent this
        cascade cannot see may be forgiven by the set door, so a really broken ref (an
        undeclared name, a cycle) in the same chain is the one to name. The leaf is
        re-walked with blind referents taken as absent; every memo the walk fills is
        dropped, since its values stand on that assumption.
        """
        saved = (dict(self._memo), dict(self._deps), dict(self.errors),
                 dict(self.null_sources), dict(self.refs_read), dict(self.dest_keys),
                 [set(reading) for reading in self._reading])
        self._blind_absent = True
        try:
            if self._expand_dest_key(key, value, chain=path, seed=seed) is not None:
                self._expand_leaf(value, path=path)
        except _LenientDefect as exc:
            return exc.reason
        except _ExpandedShapeError:
            return None
        except SettingsError as exc:
            return str(exc)
        finally:
            self._blind_absent = False
            (self._memo, self._deps, self.errors, self.null_sources,
             self.refs_read, self.dest_keys, self._reading) = saved
        return None

    def _expand_dest_key(
        self, key: str, value: StoreValue, *, chain: tuple[str, ...], seed: bool = False
    ) -> str | None:
        """The OUTPUT key for *value* — identity, EXCEPT under a dest-keyed arm.

        A :class:`BindEntry` lives in a DEST-KEYED bindings arm (R-5/R-6) where the
        destination has become the mapping KEY, so the key is the box-side path
        EXPRESSION and expands exactly as ``Bind.box`` does (``space="defer"``,
        S17). Every other value shape carries its key through verbatim.

        ⚑ Discrimination is by TYPE (``isinstance(value, BindEntry)``), never by
        the key's spelling or the value's arity — a legacy :class:`Bind` and a
        :class:`BindEntry` are both 2-element-legal with opposite meanings.

        A destination that resolves to no path RAISES — except under a ``seeded``
        map (*seed*), where a present-``None`` destination answers ``None`` and the
        caller SKIPS the layer (spec §2a: "any layer whose source/dest is ``<None>``
        is SKIPPED").  An ABSENT whole-value destination raises there too: absence
        is not ``<None>``.
        """
        if not isinstance(value, BindEntry):
            return key
        dest = self._expand_str(key, space="defer", chain=(".".join(chain),))
        if dest is None and seed:
            return None
        if dest is _ABSENT or dest is None:
            state = "an absent" if dest is _ABSENT else "a present-None"
            raise _ExpandedShapeError(
                f"Binding destination {key!r} references {state} config key; "
                f"a box destination cannot resolve to no path."
            )
        assert isinstance(dest, str)
        return dest

    def _expand_leaf(
        self, value: StoreValue, *, path: tuple[str, ...]
    ) -> StoreValue | _Absent:
        """Expand a single non-KeyStore leaf (scalar / Bind / BindEntry / list / None).

        Returns the expanded terminal, ``None`` (present-None inherited from a
        referenced key), or :data:`_ABSENT` (the caller DROPS the key). The
        ``chain`` starts at this leaf's own dotted path so a self-referential
        whole-value ``@`` is a cycle, not an infinite recurse.
        """
        chain = (".".join(path),)
        if isinstance(value, Bind):
            return self._expand_bind(value, chain=chain)
        if isinstance(value, BindEntry):
            null_refs: list[str] = []
            entry = self._expand_bind_entry(value, chain=chain, null_refs=null_refs)
            if entry is None and null_refs:
                self.null_sources[path] = tuple(null_refs)
            return entry
        if isinstance(value, str):
            if is_verbatim_text(path):
                return value
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
        raise _ExpandedShapeError(
            f"{chain[0]} declares the host source {raw!r}{became} — a BARE RELATIVE "
            f"path. A stored source must resolve on its own (spec §2a): "
            f"{BARE_RELATIVE_SOURCE_HAZARD}. Set the path key it dereferences to an "
            f"absolute path, '~/...', '$XDG_*/...' or an '@'-ref."
        )

    def _expand_bind(self, bind: Bind, *, chain: tuple[str, ...]) -> StoreValue | _Absent:
        """Expand a :class:`Bind`: ``host_src`` fully host-side; ``box_dest``
        ``@``-refs only (``$XDG``/``~`` left RAW, deferred box-side — S17).

        A whole-value ``host_src`` ref that resolves absent, or any ``host_src`` ref
        to a present-None key, gives the WHOLE Bind that state — the binding cannot
        point anywhere. ``opts`` is carried verbatim; it never holds tokens.
        """
        host = self._expand_str(bind.host, space="host", chain=chain)
        if host is _ABSENT or host is None:
            # Host src ref absent (whole-value) or None → the bind inherits it.
            return host
        box = self._expand_str(bind.box, space="defer", chain=chain)
        # A box_dest is a path EXPRESSION, not a key whose absence deletes the
        # bind: a ref in it to an absent (whole-value) or present-None key leaves
        # no destination. Raise loudly rather than emit an empty or root-relative
        # dest, which is a mount foot-gun.
        if box is _ABSENT or box is None:
            state = "an absent" if box is _ABSENT else "a present-None"
            raise _ExpandedShapeError(
                f"Bind box_dest {bind.box!r} references {state} config key; "
                f"a box destination cannot resolve to no path."
            )
        assert isinstance(host, str)
        assert isinstance(box, str)
        self._refuse_relative_host_src(bind.host, host, chain=chain)
        return Bind(host, box, bind.opts)

    def _expand_bind_entry(
        self, entry: BindEntry, *, chain: tuple[str, ...],
        null_refs: list[str] | None = None,
    ) -> StoreValue | _Absent:
        """Expand a :class:`BindEntry`: ``src`` fully host-side; ``opts`` verbatim.

        The dest-keyed counterpart of :meth:`_expand_bind`. It expands ONE half,
        because the other half — the destination — is the mapping KEY and is
        expanded by :meth:`_expand_dest_key` on the node walk (R-5/R-6). A ``src``
        that resolves ``None`` (any ref to a present-None key, spec §0) or
        ``_ABSENT`` (a whole-value ref to an absent key) makes the ENTRY that state;
        the collapse (``settings_launch._emit_bind_map``) decides what a ``None``
        entry means for its category.  *null_refs* collects the refs behind a
        ``None`` (see :meth:`_expand_str`).
        """
        src = self._expand_str(
            entry.src, space="host", chain=chain, null_refs=null_refs,
        )
        if src is _ABSENT or src is None:
            return src
        assert isinstance(src, str)
        self._refuse_relative_host_src(entry.src, src, chain=chain)
        return BindEntry(src, entry.opts)

    def _expand_str(
        self, value: str, *, space: str, chain: tuple[str, ...],
        null_refs: list[str] | None = None,
    ) -> StoreValue | _Absent:
        """Expand a single string leaf in *space* (``"host"`` or ``"defer"``).

        WHOLE-VALUE ``@``-ref (S18) → INHERIT the referent's full 3-state
        (``_ABSENT`` / ``None`` / the terminal). WHOLE-VALUE ``$VAR``, host space only
        → the value or ``_ABSENT`` (:meth:`_resolve_whole_value_var`). EMBEDDED token
        or plain literal → ``expand_expr`` substitution, where an absent ``@``-ref
        substitutes ``""`` — but ANY embedded ``@``-ref to a present-``None`` key makes
        the whole value ``None`` (spec §0, [R186]).  ⚑ Never ``""`` for that case:
        ``@key/x`` with ``key`` null would otherwise become the host path ``/x``.

        *space*: ``"host"`` expands ``~``/``$VAR`` host-side; ``"defer"`` leaves
        them RAW for the box side (S17). ``@``-refs expand in BOTH spaces, and under
        ``"defer"`` a referent's value enters as :func:`deferred_literal_expr`: it is a
        resolved terminal, so the box resolver must not read its characters as tokens.

        *null_refs*, when given, receives the refs that made the result ``None``.
        """
        ref_name = _is_whole_value_ref(value)
        if ref_name is not None:
            resolved = self._resolve_ref(ref_name, chain=(*chain, ref_name))
            if resolved is None and null_refs is not None:
                null_refs.append(ref_name)
            if space == "defer" and isinstance(resolved, str):
                return deferred_literal_expr(resolved)
            return resolved
        if space == "host":
            var_name = _is_whole_value_var(value)
            if var_name is not None:
                return self._resolve_whole_value_var(var_name)
        none_refs: list[str] = []
        expanded = self._expand_embedded(
            value, space=space, chain=chain, none_refs=none_refs,
        )
        if not none_refs:
            return expanded
        if null_refs is not None:
            null_refs.extend(none_refs)
        return None

    def _resolve_whole_value_var(self, name: str) -> StoreValue | _Absent:
        """A whole-value ``$VAR`` host-side: the value, or :data:`_ABSENT` (§6b).

        ⚑ HOST SPACE ONLY, and the guard is at the call site: under ``space="defer"``
        a ``$VAR`` is emitted VERBATIM for the BOX resolver (S17), so answering it
        here would resolve a box-side token against the HOST's environment.
        ⚑ Every REFUSING name still raises from ``resolve_var`` exactly as it does
        through the embedded path — an unset ``$AGENT``, an unknown ``$XDG_*``, an
        unknown name. :data:`~kanibako.settings.settings_resolve.UNSET` is the
        passthrough class alone, and it means the key is DROPPED rather than
        delivered empty.
        """
        value = resolve_var(name, self._ctx)
        if not isinstance(value, str):
            return _ABSENT
        return value

    # ------------------------------------------------------------------ #
    # Reference resolution — the transitive fixpoint + cycle guard       #
    # ------------------------------------------------------------------ #

    def _resolve_ref(
        self, dotted: str, *, chain: tuple[str, ...], absent_ok: bool = False,
    ) -> StoreValue | _Absent:
        """Fully resolve the value at snapshot path *dotted*, transitively (§6h).

        Reads the RAW value at *dotted*, then expands THAT value (recursing) so the
        result is a terminal. MEMOIZED by dotted path — the fixpoint. 3-state: an
        absent path → :data:`_ABSENT`; a present-None leaf → ``None``; else the
        expanded value. The depth cap (``MAX_REF_DEPTH``) bounds pathological
        non-cyclic chains.

        *chain* is the in-progress ref trail, ending in *dotted*: already checked
        and appended by the caller, mirroring ``expand_expr``'s contract.
        *absent_ok* returns :data:`_ABSENT` for an absent *dotted* in LENIENT mode too.
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
        for reading in self._reading:
            reading.add(dotted)
            reading.update(self._deps.get(dotted, ()))
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
        if raw is _ABSENT and self._derive is not None:
            derived = self._derived(dotted, chain=chain)
            if derived is not _ABSENT:
                return derived
        if raw is _ABSENT:
            if self._collect_errors and not absent_ok:
                # LENIENT (Q9): a DANGLING ref is a set-time defect to record, NOT
                # the strict §6b silent drop. Raised so the OWNING leaf gets it.
                reason = _absent_reason(dotted)
                if reason is not None:
                    raise _LenientDefect(f"dangling @-reference '@{dotted}' ({reason})")
                if not self._blind_absent:
                    raise _LenientDefect(
                        f"dangling @-reference '@{dotted}' ({_NOT_IN_CASCADE})", blind=True,
                    )
            # ⚑ Absence propagates (§6b) only from a KEY; a ref that names no key is
            # refused by name (spec §0), never dropped.
            verdict = keyspace_verdict(dotted)
            if verdict.cls is not KeyClass.KEY:
                # The key that HOLDS the ref is the trail element before it.
                leaf = self._leaf_labels[-1] if self._leaf_labels else chain[0]
                holder = leaf if len(chain) == 2 else chain[-2]
                trail = "" if holder == leaf else f" (reached from {leaf})"
                raise SettingsError(
                    f"{holder}: '@{dotted}' references no key: "
                    f"{verdict.reason}{trail}."
                )
            if not self._collect_errors:
                self._memo[dotted] = _ABSENT
            return _ABSENT
        # Resolve the referent's value AS A LEAF, with the cycle chain threaded so
        # a ref back into this path (directly or transitively) is caught.
        # ⚑ A nested KeyStore referent is degenerate, but it MUST route through
        # ``_expand_node``: a bare ``resolved = raw`` would ALIAS the input tree
        # (S19) and would leave the subtree's own tokens unexpanded.
        self._reading.append(set())
        try:
            if isinstance(raw, KeyStore):
                resolved: StoreValue | _Absent = self._expand_node(
                    raw, path=tuple(dotted.split("."))
                )
            elif isinstance(raw, Bind):
                resolved = self._expand_bind(raw, chain=chain)
            elif isinstance(raw, BindEntry):
                # The entry alone: its destination is the KEY, unreachable by value ref.
                resolved = self._expand_bind_entry(raw, chain=chain)
            elif isinstance(raw, str) and is_verbatim_text(dotted.split(".")):
                resolved = raw
            elif isinstance(raw, str):
                resolved = self._expand_str(raw, space="host", chain=chain)
            else:
                resolved = raw  # int / float / bool / None / list — verbatim terminal.
        finally:
            deps = self._reading.pop()
        self._deps[dotted] = frozenset(deps)
        for reading in self._reading:
            reading.update(deps)
        self._memo[dotted] = resolved
        return resolved

    def _derived(self, dotted: str, *, chain: tuple[str, ...]) -> StoreValue | _Absent:
        """Ask *derive* for *dotted*, its reads resolved on this pass and memoized."""
        assert self._derive is not None

        def read(key: str) -> object:
            got = self._resolve_ref(key, chain=(*chain, key), absent_ok=True)
            return __MISSING__ if got is _ABSENT else got

        self._reading.append(set())
        try:
            got = self._derive(dotted, read)
        finally:
            deps = self._reading.pop()
        if got is __MISSING__:
            return _ABSENT
        self._deps[dotted] = frozenset(deps)
        for reading in self._reading:
            reading.update(deps)
        resolved = cast(StoreValue, got)
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
        self,
        value: str,
        *,
        space: str,
        chain: tuple[str, ...],
        none_refs: list[str],
    ) -> str:
        """Substitute embedded tokens in *value* via ``expand_expr`` (§6b).

        An ``@``-ref token resolves through :meth:`_lookup_str` (absent/None →
        ``""``, a None also RECORDED in *none_refs*, and a :func:`deferred_literal_expr` under
        ``space="defer"``); ``~``/``$VAR`` expand host-side for ``space="host"`` and are
        left RAW (``defer_env=True``) for ``space="defer"`` (S17). A cycle reached
        through an embedded token still raises (B7). ONE scanner serves both spaces —
        no fork; the deferral is the engine's additive ``defer_env`` flag.
        """
        defer = space == "defer"

        def lookup(ref: str, ch: tuple[str, ...]) -> str:
            text = self._lookup_str(ref, ch, none_refs)
            return deferred_literal_expr(text) if defer else text

        return expand_expr(
            value, space="host", ctx=self._ctx, lookup=lookup, chain=chain,
            defer_env=defer,
        )

    def _lookup_str(
        self,
        dotted: str,
        chain: tuple[str, ...],
        none_refs: list[str],
    ) -> str:
        """``expand_expr`` lookup: resolve *dotted* and coerce to a SUBSTITUTION
        string (the embedded-token rule, §6b).

        Reuses the transitive resolver, so embedded refs are fixpoint- and
        cycle-guarded too (B7). STRICT: an absent referent → ``""``, an empty
        substitution that never deletes the host key. *chain* is ``expand_expr``'s
        already-extended trail.

        LENIENT (Q9): an ABSENT referent never reaches that coercion —
        ``_resolve_ref`` raises ``_LenientDefect`` first. Only the absent case
        diverges.

        A present-``None`` referent is RECORDED in *none_refs* before the coercion:
        its ``""`` is a placeholder the caller (:meth:`_expand_str`) discards, since
        the whole value is then ``None`` (spec §0, [R186]).
        """
        resolved = self._resolve_ref(dotted, chain=chain)
        if resolved is None:
            none_refs.append(dotted)
        if resolved is _ABSENT or resolved is None:
            return ""
        if isinstance(resolved, Bind):
            # Degenerate but total: a Bind has no single string form → its host.
            return resolved.host
        if isinstance(resolved, BindEntry):
            # Same, dest-keyed: the destination is the key, not part of the value.
            return resolved.src
        return str(resolved)
