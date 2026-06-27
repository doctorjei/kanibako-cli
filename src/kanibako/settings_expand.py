"""Eager build-time EXPANSION — resolve the merged snapshot's tokens to terminals.

Block 3 of the KeyStore implementation. ONE pure function, :func:`expand`, walks
block 2b's raw merged :class:`~kanibako.settings_store.KeyStore` snapshot
(``d33db5c``) and resolves every ``@``-ref (CONFIG, both bind sides) and host-side
``$VAR`` / ``~`` (ENVIRONMENT) to terminals — TRANSITIVELY (fixpoint /
topological), with cycle detection. It is PURE: no file / env / clock access, same
input → same output, and it NEVER mutates the input snapshot (S19) — it builds a
fresh :class:`~kanibako.settings_store.KeyStore`.

It REUSES the existing single-expr engine
:func:`kanibako.settings_resolve.expand_expr` (the scanner: escapes, ``~``,
``$VAR``/``${VAR}``, ``@ref``, the ``chain`` cycle-guard, ``MAX_REF_DEPTH``) and
adds the three things that engine lacks (brief §3):

1. **Snapshot-backed TRANSITIVE lookup.** The ``lookup`` callback resolves a ref
   by reading the snapshot at that dotted path AND fully expanding THAT value
   first (recursing), so chains collapse to terminals regardless of dict order.
   Reuses ``_expand_ref``'s ``chain``-based cycle guard. Results are MEMOIZED, so
   the pass is a fixpoint, not re-resolution per reference.
2. **Whole-value ``@``-ref 3-state propagation (§6b/§6h).** A value that IS
   exactly one ``@x`` (decided by PARSE — S18, never guessed) inherits the
   referent's 3-state through every link: referent absent → this key ABSENT
   (dropped from the snapshot); referent present-``None`` → ``None`` (kept, the
   §3 terminal a bind/category consumer then OMITs); else the terminal value. An
   EMBEDDED token (``@x`` inside a larger string) is pure SUBSTITUTION via
   ``expand_expr`` (absent/None → empty string; never deletes the key).
3. **CONFIG-vs-ENV deferral (§6a/B6 — S17).** For a :class:`Bind`: ``host_src``
   expands FULLY host-side (``@``-refs + ``$XDG``/``~``). ``box_dest`` expands its
   ``@``-refs (CONFIG, same both sides) but leaves ``$XDG``/``~`` (ENVIRONMENT,
   host ≠ box) RAW — a DEFERRED token resolved box-side at mount. The expanded
   ``Bind.box`` may therefore still carry a ``$XDG``/``~`` token: a known, bounded
   residue, NOT lazy config re-resolution.

Cycle = hard build ERROR (``SettingsError``), covering whole-value AND embedded
tokens (B7), with the chain in the message — KEPT DISTINCT from a legitimately
absent/None referent (that is §6b propagation, NOT an error).

OUT of scope (hard boundaries): NO cascade merge / precedence (block 2b — this
consumes its output), NO ``reconcile_categories`` / ``box_dest`` collision (§6g
separate pass), NO typed views (block 4), NO ``config set`` (block 5), NO consumer
swap (block 7). It does NOT modify ``expand_expr`` / ``resolve_value`` /
``SettingsResolver`` / ``start.py`` — it wraps + builds ALONGSIDE them.

Authority
---------
* ``~/vault/rw/keystore-design.md`` §6h (transitive expansion + cycle — PRIMARY),
  §6a (CONFIG-vs-ENV split; box-side ``$XDG``/``~`` deferred), §6b (whole-value vs
  embedded ``@``-ref shapes), §3 (3-state).
* Spec ``settings-keyspace-1.6.0-target.md`` §0, §1 (box-side XDG line ~94), §2c.

Seams realized here (``plans/keystore-blocks/SEAMS.md``)
-------------------------------------------------------
* **S17** — box-side ``$XDG``/``~`` left RAW in ``Bind.box``; ``@``-refs expand
  BOTH sides. The concrete realization of S12's deferral contract.
* **S18** — whole-value vs embedded ``@``-ref decided by PARSE, never by guess.
* **S19** — expansion does NOT mutate the input snapshot (pure; fresh tree).
* **S3** — every snapshot access uses the UNBOUND ``dict.<method>(obj, …)`` bypass
  (a key named ``get`` / ``items`` cannot shadow the protocol into a crash).
"""

from __future__ import annotations

from kanibako.settings_resolve import (
    _REF_NAME_RE,
    MAX_REF_DEPTH,
    ResolveCtx,
    SettingsError,
    expand_expr,
)
from kanibako.settings_store import Bind, KeyStore, StoreValue


class _Absent:
    """Sentinel: a ref resolved to a LEGITIMATELY ABSENT key (§6b propagation).

    Distinct from a *cycle* (which raises) and from a stored ``None`` (present-
    None, a real terminal). A whole-value ``@``-ref to an absent key propagates
    THIS sentinel up the chain; at the top it drops the host key from the
    snapshot. An EMBEDDED token coerces it to ``""``. Module-private, never
    stored, never a member of :data:`~kanibako.settings_store.StoreValue`.
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


def _is_whole_value_ref(value: str) -> str | None:
    """Return the dotted ref name iff *value* IS exactly one whole-value ``@``-ref.

    S18 — the shape is decided by PARSE, never guessed: a value is whole-value iff
    it is ``@`` followed by a dotted ref name (``_REF_NAME_RE``) and NOTHING else
    (no leading/trailing characters, no embedded literal). ``"@a.b"`` → ``"a.b"``;
    ``"@a-@b"`` / ``"x@a"`` / ``"@a/c"`` / ``"@a "`` → ``None`` (embedded — handled
    by :func:`~kanibako.settings_resolve.expand_expr` substitution). A leading
    ``~`` or ``$`` is therefore never whole-value (those are environment tokens,
    not config refs).
    """
    if not value or value[0] != "@":
        return None
    m = _REF_NAME_RE.match(value, 1)
    if m is None or m.end() != len(value):
        return None
    return m.group(0)


def expand(snapshot: KeyStore, ctx: ResolveCtx) -> KeyStore:
    """Expand *snapshot*'s tokens to terminals, returning a FRESH KeyStore (S19).

    *snapshot* is block 2b's raw merged store (refs/vars/``~`` intact). *ctx*
    carries the host-side expansion namespace (``host_home``, ``xdg``,
    ``agent_name``, ``workset_name``) consumed for host-side ``$VAR`` / ``~``.

    Every value is resolved TRANSITIVELY to a fixpoint (§6h): an ``@``-ref reads
    the snapshot at its dotted path and expands THAT value first, so a multi-hop
    chain (``A=@B``, ``B=@C``, ``C=term``) collapses to ``term`` regardless of
    dict order. Per leaf:

    * **scalar str** → expanded host-side (``space="host"``); a whole-value
      ``@``-ref inherits the referent's 3-state (absent → the key is DROPPED;
      present-None → ``None``); an embedded token substitutes per ``expand_expr``.
    * **Bind** → ``host_src`` expanded FULLY host-side; ``box_dest`` expands its
      ``@``-refs but leaves ``$XDG``/``~`` RAW (deferred box-side, S17). If a
      whole-value ``host_src`` ``@``-ref resolves absent/None, the WHOLE Bind is
      dropped / carried as that terminal (§3 — a bind/category consumer OMITs it).
    * **non-str scalar** (``int`` / ``float`` / ``bool`` / ``None`` / ``list``) →
      carried verbatim (no token to expand).

    A CYCLE (whole-value or embedded — B7) raises :class:`SettingsError` with the
    chain; this is DISTINCT from a legitimately absent/None referent (propagated,
    not raised). The input snapshot is never mutated (S19).
    """
    expander = _Expander(snapshot, ctx)
    return expander.run()


class _Expander:
    """The per-pass expansion state: the source snapshot, ctx, and the memo.

    Holds the fixpoint memo of fully-resolved values keyed by dotted snapshot
    path. One instance per :func:`expand` call (pure — no cross-call state). The
    snapshot is read-only here (S19); the fresh tree is built in :meth:`run`.
    """

    def __init__(self, snapshot: KeyStore, ctx: ResolveCtx) -> None:
        self._snapshot = snapshot
        self._ctx = ctx
        # Memo: dotted path -> fully-resolved value (or _ABSENT). A path mid-
        # resolution is NOT in the memo; the ``chain`` argument detects a cycle
        # before the memo would (a self-revisit). None and _ABSENT are valid memo
        # values (present-None terminal; legitimately-absent ref), so absence
        # from the memo is tested with ``in``, never by a sentinel value.
        self._memo: dict[str, StoreValue | _Absent] = {}

    # ------------------------------------------------------------------ #
    # Tree walk — build the fresh expanded snapshot                      #
    # ------------------------------------------------------------------ #

    def run(self) -> KeyStore:
        """Walk the source snapshot and build the fresh expanded tree (S19)."""
        return self._expand_node(self._snapshot, path=())

    def _expand_node(self, node: KeyStore, *, path: tuple[str, ...]) -> KeyStore:
        """Build a fresh expanded KeyStore mirroring *node* at *path*.

        A child KeyStore recurses; a leaf is expanded via :meth:`_expand_leaf`. A
        whole-value ``@``-ref leaf that resolves ABSENT is DROPPED (§6b) — the key
        simply does not appear in the output node. Uses the UNBOUND ``dict``
        protocol (S3) so a key named ``keys`` / ``items`` / ``get`` cannot shadow.
        """
        out = KeyStore()
        for key in dict.keys(node):
            child_path = (*path, key)
            value = dict.__getitem__(node, key)
            if isinstance(value, KeyStore):
                out[key] = self._expand_node(value, path=child_path)
                continue
            resolved = self._expand_leaf(value, path=child_path)
            if resolved is _ABSENT:
                continue  # whole-value ref to an absent key → drop this key (§6b).
            out[key] = resolved
        return out

    def _expand_leaf(
        self, value: StoreValue, *, path: tuple[str, ...]
    ) -> StoreValue | _Absent:
        """Expand a single non-KeyStore leaf (scalar / Bind / list / None).

        Returns the expanded terminal, ``None`` (present-None inherited from a
        whole-value ref), or :data:`_ABSENT` (whole-value ref to an absent key →
        the caller DROPS the key). The ``chain`` starts at this leaf's own dotted
        path so a self-referential whole-value ``@`` to the leaf's own key is a
        cycle, not an infinite recurse.
        """
        chain = (".".join(path),)
        if isinstance(value, Bind):
            return self._expand_bind(value, chain=chain)
        if isinstance(value, str):
            return self._expand_str(value, space="host", chain=chain)
        # int / float / bool / None / list[str] — no token to expand, carried
        # verbatim. (A present-None stored leaf is a real terminal, not _ABSENT.)
        return value

    def _expand_bind(self, bind: Bind, *, chain: tuple[str, ...]) -> StoreValue | _Absent:
        """Expand a :class:`Bind`: ``host_src`` fully host-side; ``box_dest``
        ``@``-refs only (``$XDG``/``~`` left RAW, deferred box-side — S17).

        If the ``host_src`` is a whole-value ``@``-ref that resolves absent/None,
        the WHOLE Bind takes that 3-state (the binding cannot point anywhere): an
        absent host → ``_ABSENT`` (drop the bind); a present-None host → ``None``
        (the §3 bind/category OMIT terminal). Otherwise both halves are strings;
        ``opts`` is carried verbatim (it never holds tokens).
        """
        host = self._expand_str(bind.host, space="host", chain=chain)
        if host is _ABSENT or host is None:
            # Whole-value host ref absent/None → the bind inherits that 3-state.
            return host
        box = self._expand_str(bind.box, space="defer", chain=chain)
        # A box_dest is a path EXPRESSION, not a key whose absence deletes the bind
        # — so it never returns _ABSENT/None from the EMBEDDED path (an embedded
        # token coerces to ""). The ONLY way box is _ABSENT/None here is a
        # WHOLE-VALUE box_dest @-ref to an absent/present-None config key. The spec
        # has NO whole-value box_dest (every box_dest is ~/… or $XDG… or an embedded
        # @-path), so this is an unreachable-on-spec-forms config error; rather than
        # silently emit an empty dest (a mount foot-gun), raise loudly with the bind.
        if box is _ABSENT or box is None:
            state = "absent" if box is _ABSENT else "present-None"
            raise SettingsError(
                f"Bind box_dest is a whole-value @-reference to an "
                f"{state} config key ({bind.box!r}); a box destination cannot "
                f"resolve to no path."
            )
        assert isinstance(host, str)
        assert isinstance(box, str)
        return Bind(host, box, bind.opts)

    def _expand_str(
        self,
        value: str,
        *,
        space: str,
        chain: tuple[str, ...],
    ) -> StoreValue | _Absent:
        """Expand a single string leaf in *space* (``"host"`` or ``"defer"``).

        WHOLE-VALUE ``@``-ref (S18) → resolve the referent's full 3-state and
        INHERIT it (``_ABSENT`` / ``None`` / the terminal). EMBEDDED token (or a
        plain literal) → :func:`~kanibako.settings_resolve.expand_expr`
        substitution (absent/None token → empty string), in the given space.

        *space*: ``"host"`` expands ``~``/``$VAR`` host-side (``host_src``,
        scalars); ``"defer"`` leaves ``~``/``$VAR`` RAW (box-side env, S17) while
        still expanding ``@``-refs. ``@``-refs (CONFIG) expand in BOTH spaces.
        """
        ref_name = _is_whole_value_ref(value)
        if ref_name is not None:
            # Whole-value: inherit the referent's 3-state the full chain (§6b/§6h).
            return self._resolve_ref(ref_name, chain=(*chain, ref_name))
        # Embedded token / plain literal → scanner substitution. The lookup
        # coerces absent/None to "" (embedded rule, §6b); a cycle still raises.
        return self._expand_embedded(value, space=space, chain=chain)

    # ------------------------------------------------------------------ #
    # Reference resolution — the transitive fixpoint + cycle guard       #
    # ------------------------------------------------------------------ #

    def _resolve_ref(
        self, dotted: str, *, chain: tuple[str, ...]
    ) -> StoreValue | _Absent:
        """Fully resolve the value at snapshot path *dotted*, transitively (§6h).

        Reads the RAW value at *dotted* in the source snapshot, then expands THAT
        value (recursing through its own ``@``-refs) so the result is a terminal.
        MEMOIZED by dotted path (the fixpoint). 3-state: an absent path →
        :data:`_ABSENT`; a present-None leaf → ``None``; else the expanded value.

        *chain* is the in-progress ref trail (ending in *dotted*) for the cycle
        guard: it was already checked + appended by the caller (``_expand_ref`` /
        :meth:`_expand_str`), mirroring ``expand_expr``'s contract. The depth cap
        (``MAX_REF_DEPTH``) bounds pathological non-cyclic chains.
        """
        # CYCLE GUARD (B7 — covers whole-value AND embedded paths). *chain* ends in
        # *dotted* (the caller appended it, mirroring ``expand_expr``'s contract); a
        # PRIOR occurrence of *dotted* in the chain means we re-entered a ref still
        # in progress → a cycle, raised with the full trail. Checked BEFORE the memo
        # so a cycle is never masked by a half-built memo entry (none is stored
        # mid-resolution anyway).
        if dotted in chain[:-1]:
            cycle = " -> ".join(chain)
            raise SettingsError(f"Cyclic @-reference: {cycle}")
        if dotted in self._memo:
            return self._memo[dotted]
        if len(chain) > MAX_REF_DEPTH:
            raise SettingsError(
                f"@-reference depth cap ({MAX_REF_DEPTH}) exceeded resolving "
                f"'{dotted}'."
            )
        raw = self._lookup_raw(dotted)
        if raw is _ABSENT:
            self._memo[dotted] = _ABSENT
            return _ABSENT
        # Resolve the referent's value AS A LEAF, with the cycle chain threaded so
        # a ref back into this path (directly or transitively) is caught. A nested
        # KeyStore referent (a whole subtree) is degenerate — the spec never refs a
        # whole subtree — but it MUST be resolved through ``_expand_node`` so the
        # returned subtree is (a) FRESH, not an alias of the input (S19 — a bare
        # ``resolved = raw`` would make ``out[...] is snapshot[...]`` and let a later
        # output edit leak back into a partial), and (b) fully EXPANDED (its inner
        # ``@``/``$`` tokens resolved), matching how the same subtree is expanded at
        # its own location. The dotted path seeds child cycle chains.
        if isinstance(raw, KeyStore):
            resolved: StoreValue | _Absent = self._expand_node(
                raw, path=tuple(dotted.split("."))
            )
        elif isinstance(raw, Bind):
            resolved = self._expand_bind(raw, chain=chain)
        elif isinstance(raw, str):
            resolved = self._expand_str(raw, space="host", chain=chain)
        else:
            resolved = raw  # int / float / bool / None / list — verbatim terminal.
        self._memo[dotted] = resolved
        return resolved

    def _lookup_raw(self, dotted: str) -> StoreValue | _Absent:
        """Read the RAW (unexpanded) value at snapshot path *dotted*, 3-state.

        Walks the dotted segments with the UNBOUND ``dict.get(node, seg, _ABSENT)``
        probe (S3): any missing segment, or a non-KeyStore node reached before the
        last segment, yields :data:`_ABSENT` (the path does not exist). The final
        segment's value is returned verbatim (a present-``None`` leaf → ``None``).
        """
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
        through an embedded token still raises (B7 — the chain guard is in
        ``expand_expr``'s ``_expand_ref`` AND in :meth:`_resolve_ref`).

        Reuses the SINGLE ``expand_expr`` scanner for BOTH spaces (no fork): the
        box-side deferral is the engine's additive ``defer_env`` flag, proposed in
        chat and held pending the director's call.
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

        Reuses the transitive resolver (so embedded refs are also fixpoint /
        cycle-guarded — B7). An absent or present-None referent → ``""`` (empty
        substitution, never deletes the host key). A resolved scalar/Bind/list →
        its string form. *chain* is ``expand_expr``'s already-extended trail.
        """
        resolved = self._resolve_ref(dotted, chain=chain)
        if resolved is _ABSENT or resolved is None:
            return ""
        if isinstance(resolved, Bind):
            # An embedded ref to a whole Bind is degenerate, but be total: a Bind
            # has no single string form, so substitute its host (the source path).
            return resolved.host
        return str(resolved)
