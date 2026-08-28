"""How resolved config is RENDERED — the ``show`` / ``--effective`` blocks.

``config show`` answers a different question from ``config get``: not "what is
stored at this noun" but "what does this box actually see, and WHY".  The why is
the whole design — each ``pref`` request printed beside the value it produced,
each abstract declaration printed above the binding it derives — so that "I set
it and nothing happened" is answerable from the output instead of by reading
files.  These renderers are the machinery for that, plus the two flatteners that
recover nested settings tables the flat override view cannot see.

⚑ RENDERING ONLY — no key semantics, no destinations, no re-derivation.  Both
halves of every paired display are read off the SAME snapshot the launch
resolved: a display that recomputed anything would be a second opinion about
what the box sees, which is the failure this output exists to detect.  Whether a
string IS a key is :mod:`kanibako.settings.settings_keyspace` (spec §0), which
family it belongs to is :mod:`kanibako.settings.config_keys`, and where its value
lives is :mod:`kanibako.settings.config_dest`; nothing here answers any of those.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from kanibako.settings.config_io import load_doc
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.settings_prefs import PREF_ROOT


def _nested_settings_overrides(path: Path | None) -> dict[str, str]:
    """Flatten a settings file's nested SCOPE tables to ``dotted.key → value``.

    The display companion of the ``_SETTINGS_SCOPE_TOKENS`` routing (F2): a
    ``config set`` at the SYSTEM scope nests scope-token settings (e.g.
    ``system.auth.share_allowed``, downward ``workset.*``/``box.*`` defaults)
    in the system SETTINGS file — entries the flat ``KanibakoConfig`` override
    view cannot see.  Flattens every top-level scope table EXCEPT ``agent``
    (rendered by the agent-settings view).  Bools render lowercase, matching
    ``get``.
    """
    if path is None or not path.exists():
        return {}
    data = load_doc(path)
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}

    def _walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            if isinstance(v, dict):
                _walk(v, f"{prefix}{k}.")
            elif isinstance(v, bool):
                out[f"{prefix}{k}"] = str(v).lower()
            else:
                out[f"{prefix}{k}"] = str(v)

    for key, val in data.items():
        # ``resource_overrides`` is the LEGACY dead table of the dropped
        # ``resource.*`` surface (spec §3 D-M7): the settable code is gone, so a
        # pre-1.7.x file may still carry an inert table.  Skip it here so it never
        # renders in ``system show``/``--effective`` — display-only, not a revived
        # settable surface.
        if key in ("agent", "resource_overrides") or not isinstance(val, dict):
            continue
        _walk(val, f"{key}.")
    return out


def _pref_overrides(path: Path | None) -> dict[str, str]:
    """Flatten a settings file's ``pref:`` table to ``pref.<target> -> value``.

    ``config show`` must LIST prefs (spec §2h read verbs). The box/workset plain
    view reads ``load_project_overrides`` + ``read_agent_settings``, neither of
    which can see a ``pref:`` table, so it is flattened here with the SAME walk
    ``_nested_settings_overrides`` uses. A present-``None`` request renders as
    ``null`` — it is a REQUEST TO SUPPRESS, and showing it as blank would make
    the one thing a box cannot otherwise express look like nothing at all.
    """
    if path is None or not path.exists():
        return {}
    data = load_doc(path)
    if not isinstance(data, dict):
        return {}
    table = data.get(PREF_ROOT)
    if not isinstance(table, dict):
        return {}
    out: dict[str, str] = {}

    def _walk(node: dict, prefix: str) -> None:
        for k, v in node.items():
            if isinstance(v, dict):
                _walk(v, f"{prefix}{k}.")
            elif isinstance(v, bool):
                out[f"{prefix}{k}"] = str(v).lower()
            elif v is None:
                out[f"{prefix}{k}"] = "null"
            else:
                out[f"{prefix}{k}"] = str(v)

    _walk(table, f"{PREF_ROOT}.")
    return out


def _print_pref_block(snapshot: Any, out: Any) -> None:
    """Render each ``pref`` REQUEST beside the RESULT it produced (spec §2h).

    *"--effective shows BOTH the request and the resulting value — so 'why did
    system.agent resolve to zippity' is answerable from the snapshot instead of
    by reading files. This is what closes the 'I set it and nothing happened'
    failure family."*

    Both halves come off the SAME snapshot, and that is exactly why ``expand``
    carries the ``pref`` subtree through UNEXPANDED: the request is readable in
    the form it was WRITTEN (``@meta.workset.path/tpl``) while the target holds
    the resolved terminal. Rendering an expanded request beside its result would
    print the same string twice and answer nothing.

    ⚑ THE REQUEST WALK IS ``settings_prefs``' OWN, not a second one. That module
    stops the walk at a TERMINAL dest-keyed category (``masks``,
    ``bindings.{ro,rw}``, ``caches``/``seeded``/``common``/``synced``) because the
    keys inside one are DESTINATIONS — data, not key segments — and a real
    destination contains dots. A private walk here descended past that stop and
    then split the target on ``.``, which SHATTERED every dotted destination and
    reported a present, working entry as suppressed. Delegating keeps the
    terminal question in the one module that owns it (no key semantics here); the
    per-entry expansion below reads the dest as a MAP KEY on both halves and
    never re-splits it.
    """
    from kanibako.settings.kb_store import Bind, BindEntry
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.settings_prefs import prefs_from_partial

    if not isinstance(snapshot, KeyStore):
        return
    # *level* / *path* only name the file in the dotted-key refusal, which cannot
    # fire here: a ``pref`` table is dropped at assembly outside workset/box, and
    # both those files were already flattened by ``collect_prefs`` during the
    # build that produced this snapshot — so a dotted name stopped the LAUNCH.
    requests = prefs_from_partial(snapshot, level="resolved")
    if not requests:
        return

    def _render(value: Any, dest: str | None = None) -> str:
        if isinstance(value, BindEntry):
            # Dest-keyed: the destination is the KEY the caller walked in with.
            opts = f"  [{value.opts}]" if value.opts else ""
            return f"{value.src} -> {dest}{opts}"
        if isinstance(value, Bind):
            opts = f"  [{value.opts}]" if value.opts else ""
            return f"{value.host} -> {value.box}{opts}"
        if value is None:
            return "null"
        return str(value)

    def _at(target: str) -> Any:
        """The RESULT node at *target*, read in the same snapshot; ``__MISSING__`` if absent."""
        cur: Any = snapshot
        for seg in target.split("."):
            if not isinstance(cur, KeyStore) or dict.get(cur, seg, __MISSING__) is __MISSING__:
                return __MISSING__
            cur = dict.get(cur, seg)
        return cur

    # One row per printable request: (display target, rendered request, result,
    # dest). A TERMINAL arm arrives as ONE request carrying the WHOLE map, and
    # expands to a row per entry — that is what keeps per-entry suppression
    # visible as such, since the arm itself survives a suppressed entry.
    rows: list[tuple[str, str, Any, str | None]] = []
    for req in requests:
        if isinstance(req.value, KeyStore):
            arm = _at(req.target)
            # ⚑ NOT named ``dest``: the row tuple unpacked below binds a ``dest``
            # of its own that is ``str | None`` (a scalar request has no
            # destination), and one name for two types is how a None-carrying row
            # gets read as an entry key.
            for entry_dest in dict.keys(req.value):
                rows.append((
                    f"{req.target}.{entry_dest}",
                    _render(dict.__getitem__(req.value, entry_dest), entry_dest),
                    dict.get(arm, entry_dest, __MISSING__) if isinstance(arm, KeyStore)
                    else __MISSING__,
                    entry_dest,
                ))
        else:
            rows.append((req.target, _render(req.value), _at(req.target), None))

    print("", file=out)
    for target, request, value, dest in sorted(rows, key=lambda r: r[0]):
        print(f"  {PREF_ROOT}.{target} = {request}", file=out)
        if value is __MISSING__:
            # The ordinary present-None rule OMITTED it: a bind / category /
            # masks leaf was suppressed. Saying so is the whole point — this is
            # the difference between "suppressed" and "unset". Name the CURE
            # too (B-6): suppression has no verb of its own, so the only place a
            # user learns what undoes it is a message like this one.
            #
            # ⚑⚑ THE CURE NAMES THE FILE, NOT A VERB (Jei, 2026-08-08e). It used to
            # spell ``reset pref.<target>.<dest>``, a command that does not work and
            # is not going to: individually reading or writing one facet of a
            # multi-faceted (dest-keyed) key does not make sense, and the access form
            # for one is a backlogged promise of unknown shape. Prescribing a verb
            # that will be refused is the same F6 lie as promising a read that has no
            # route — so this says what the retired-route refusals already say,
            # "edit the table in the settings file".
            #
            # ⚑ "at the scope that set it" is not vagueness — it is the only
            # honest form available here. Both halves of this block are read off
            # the MERGED snapshot, which no longer carries which file wrote the
            # request, and an edit made at the wrong noun removes nothing. Naming a
            # specific scope would be a guess dressed as an instruction.
            result = (
                f"(omitted — the entry is suppressed; no mount. Undo by removing "
                f"this entry from the '{PREF_ROOT}:' table of the settings file "
                f"at the scope that set it)"
            )
        elif value is None:
            result = "(unset — the consumer applies its default)"
        else:
            result = _render(value, dest)
        print(f"    -> {target} = {result}", file=out)


def _print_category_block(
    snapshot: Any, error: str | None, out: Any, box_ctx: Any,
    declared_by: "Mapping[str, str] | None" = None,
) -> None:
    """Render the ``--effective`` PATH-DELIVERY block (spec §0; box scope, D6).

    The pid-0 FOUNDATION comes first, then every CONCRETE binding with the
    destination it occupies — all read off the SAME snapshot the launch resolved.
    Nothing is re-derived here.

    *box_ctx* is the launch's own :class:`~kanibako.settings.settings_resolve.ResolveCtx`
    (``agent_select.launch_resolve_ctx``, the ONE builder), needed because an arm KEY
    still spells the destination the user WROTE while the arbitrated map is keyed by
    the resolved guest path — see the pairing below.

    *declared_by* is the SAME launch's ``LaunchDeliveries.declared_by`` — the fold's
    own record of which declaration took each destination — and it is what lets a LOSS
    name the key that beat it rather than only the path.  ⚑ IT IS HANDED IN, and that
    is the whole point: this display folds IN PROCESS (``commands.box._parser`` builds
    the snapshot per command), so the map exists at the moment the block runs; the
    collapsed leaf it is paired against carries no key and may not be taught to
    (``store_collapse.CollapsedStore``).  Omitted, every phrase is exactly what it was.

    The ABSTRACT half then lists every ``common`` / ``caches`` / ``seeded``
    declaration with what the box RECEIVES for it indented beneath — keyspec
    ``:88``, *"``--effective`` shows BOTH the declaration and the derived binding
    and a user can see WHY a mount exists."*  The pairing is
    :func:`kanibako.settings.settings_categories.effective_bindings_and_template_sources`,
    the single source; nothing is re-derived here either.
    """
    from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE, BindEntry
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.settings_categories import (
        MOUNT,
        effective_bindings_and_template_sources,
    )
    from kanibako.settings.settings_launch import (
        BOX_HOME_KEY,
        resolve_box_dest,
        snapshot_leaf,
    )
    from kanibako.settings.settings_resolve import normalize_bind_dest
    from kanibako.settings.store_collapse import (
        DERIVED_MOUNT,
        HOME_DEST,
        Declaration,
        derivation_result,
        pair_declarations,
    )

    print("", file=out)
    if error is not None:
        for line in error.splitlines():
            print(f"  {line}" if line else "", file=out)
        return

    def _leaf(dotted: str) -> Any:
        node: Any = snapshot
        for seg in dotted.split("."):
            if not isinstance(node, KeyStore):
                return None
            node = dict.get(node, seg, None)
        return node

    # The pid-0 FOUNDATION, FIRST — and it is not a scope declaration, which is why
    # it is labelled rather than spelled as a key.  The box home does NOT route
    # through ``bindings.rw`` (spec ``:1015``): the assembly seam builds it from the
    # RO DERIVED ``meta.box.home``, so the per-scope walk below cannot see it, and
    # without this line the one mount EVERY box has would be missing from the view
    # that exists to show what a box gets.
    # ⚑ NO OPTIONS COLUMN.  Home's mount options are SEAM MACHINERY (spec ``:1015``),
    # not a facet of any key — there is nothing here a user could set, and printing
    # the string would put a second copy of the seam's literal in the display.
    home_src = _leaf(BOX_HOME_KEY)
    if isinstance(home_src, str) and home_src:
        print(f"  (foundation) {BOX_HOME_KEY} = {home_src} -> {HOME_DEST}", file=out)

    # CONCRETE next — the source of truth a mount is emitted from, PAIRED with what
    # the collapse decided for its destination.
    #
    # ⚑ The arm is DEST-KEYED (R-5/R-6): the map KEY is the box destination and
    # the leaf is a 2-element ``BindEntry(src, opts)`` carrying no destination at
    # all, so the pair is assembled from the key and the leaf TOGETHER. The leaf
    # test is ``isinstance`` and never arity — a legacy 3-tuple ``Bind`` and a
    # ``BindEntry`` are both legally 2 elements with OPPOSITE meanings
    # (``kb_store.BindEntry``, the arity trap). A leaf that is neither is a
    # malformed arm the launch already refused, which is why the display is
    # showing *error* instead of reaching here. A ``None`` leaf is a SUPPRESSED
    # entry and is skipped by the same test — it declares no mount to pair.
    rows: list[tuple[str, str, BindEntry]] = []
    for scope in ("system", "agent", "workset", "box"):
        scope_node = _leaf(scope)
        if not isinstance(scope_node, KeyStore):
            continue
        for tier, prefix in _iter_agent_tiers(scope, scope_node):
            for mode in ("ro", "rw"):
                mode_node = _sub(tier, ("bindings", mode))
                if not isinstance(mode_node, KeyStore):
                    continue
                for dest in sorted(dict.keys(mode_node)):
                    entry = dict.__getitem__(mode_node, dest)
                    if isinstance(entry, BindEntry):
                        rows.append(
                            (f"{prefix}.bindings.{mode}.{dest}", dest, entry),
                        )

    # 🛑🛑 A CONCRETE ROW IS NOT SELF-EVIDENTLY A MOUNT, and printing it as one was
    # the measured defect this pairing closes. Bind-versus-mask at one destination is
    # SUPERSESSION, not a collision (spec ``:146``): whichever arrives against the
    # already-collapsed state deletes the other, and a mask arriving second deletes
    # the bind — at its point, or from ABOVE it, where the sweep leaves the bind's own
    # destination absent from the map entirely. The arm still holds the declaration
    # either way, so a walk that renders the arm alone printed a delivery arrow for a
    # destination the box sees NOTHING at, at rc 0, with the mask that took it printed
    # nowhere. ⚑ It runs BOTH WAYS — a box binding legitimately supersedes a
    # lower-scope mask — so the answer may only come from the ARBITRATED map, never
    # from the presence of a mask among the declarations.
    #
    # ⚑ THE SAME PAIRING THE ABSTRACT HALF USES, and the same one
    # ``commands.workset_cmd._print_effective_shares`` builds for a workset's shares:
    # ONE decision function, fed a ``Declaration`` per row. Nothing is re-derived —
    # ``meta.assembly.bindings`` is READ, and ``covering_bind`` inside the pairing owns
    # the containment and its separator guard.
    bindings = snapshot_leaf(snapshot, "meta.assembly.bindings")
    collapsed = dict(bindings) if isinstance(bindings, dict) else {}
    # ⚑⚑ THE ARM KEY IS THE DESTINATION AS WRITTEN; THE ARBITRATED MAP IS KEYED BY THE
    # RESOLVED ONE. The eager build defers ``~`` and ``$VAR`` in a destination, so an
    # arm holds ``$XDG_DATA_HOME/z`` while the map holds ``/data/z`` — two spellings of
    # one destination. Both sides are put in the map's spelling before pairing, or a
    # row is looked up under a name nothing was ever filed under and every answer about
    # it is a miss. ⚑ Resolution BEFORE binding OR comparison is the rule (Jei,
    # 2026-08-27), and this is the comparison half of it.
    #
    # ⚑ NEITHER STEP IS A RE-DERIVATION. ``resolve_box_dest`` is the ONE box_dest
    # expansion — the same call ``snapshot_category_entries`` fed the collapse from —
    # and ``normalize_bind_dest`` is the ONE canonicalizer the map is keyed by,
    # idempotent by contract (R-11 applies it at every producer AND again on read).
    #
    # ⚑ IT CANNOT RAISE HERE. Every arm key reached the collapse through the same
    # expansion under the same ctx, so a dest this would refuse (an unknown ``$VAR``,
    # a surviving ``@``-ref) stopped the LAUNCH before any display ran — measured.
    declarations = [
        Declaration(
            key=key,
            dest=normalize_bind_dest(resolve_box_dest(dest, box_ctx)),
            src=entry.src,
            delivery=MOUNT,
        )
        for key, dest, entry in rows
    ]
    derivations = dict(zip(
        [declaration.key for declaration in declarations],
        pair_declarations(declarations, collapsed),
        strict=True,
    ))
    for key, dest, entry in rows:
        opts = f"  [{entry.opts}]" if entry.opts else ""
        derivation = derivations[key]
        # ⚑ THE ARROW IS THE DELIVERY, and only a delivered binding earns one — the
        # rule ``workset share list --effective`` already renders by. A row that
        # receives nothing keeps its KEY (that key is what a user edits) and is
        # printed in DECLARATION form with the reason beneath it, so a reader skimming
        # the block for mounts cannot take a loss for one.
        # ⚑ THE DESTINATION PRINTS AS THE USER WROTE IT — the key they edit is the
        # arm's, and an answer spelled in a form absent from their files is one they
        # cannot act on. The RESOLVED path is what the pairing decided on, and it is
        # already in the reason line beneath a loss.
        if derivation.outcome == DERIVED_MOUNT:
            print(f"  {key} = {entry.src} -> {dest}{opts}", file=out)
            continue
        print(f"  {key} = {dest}{opts}  (declared: {entry.src})", file=out)
        # ⚑ THE SAME KEYS THE ABSTRACT HALF BELOW GETS. A concrete row loses to a mask
        # exactly as an abstract one does — it is the case this whole pairing was added
        # for — so keying one half and not the other would leave the acute case bare.
        print(f"    {derivation_result(derivation, declared_by)}", file=out)

    # ABSTRACT declarations, each with THE DELIVERY THE BOX ACTUALLY RECEIVES.
    #
    # 🛑🛑 THIS IS NOT A READ OF ``binding_derivations``, and it may never become
    # one. That node is populated BEFORE arbitration, deliberately (R-8), so every
    # row in it reads as a live mount — including rows for declarations the box
    # receives NOTHING for. A renderer reading it alone prints ``(mount)`` for a
    # ``common`` declaration a mask has swallowed, and prints no mask at all: the
    # silent half, and the measured reason this block was disabled rather than
    # left to guess. The pairing against ``meta.assembly.*`` is what makes the
    # answer the BOX'S, and it lives in ONE function — recomputing either half
    # here is the second opinion ``--effective`` exists to DETECT.
    #
    # ⚑ ``seeded`` derives a COPY, not a mount, and that distinction is carried by
    # the outcome rather than restated here (``settings_categories
    # .declaration_delivery`` is its one definition).
    #
    # ⚑ THE RESULT PHRASES MOVED OUT — ``store_collapse.derivation_result``, beside
    # the ``DERIVED_*`` outcomes they name. ``workset share list --effective`` is a
    # second reader of the same pairing, and one sentence about what a mask did to a
    # declaration is not a thing to keep two copies of.
    for row in effective_bindings_and_template_sources(snapshot):
        print(f"  {row.declaration.key} = {row.declaration.src}", file=out)
        print(
            f"    {BINDING_DERIVATIONS_NODE}.{row.declaration.key} = "
            f"{derivation_result(row, declared_by)}",
            file=out,
        )


def _iter_agent_tiers(scope: str, scope_node: Any):
    """``(node, key-prefix)`` per DISCRIMINATED tier of *scope*.

    Only the agent scope has tiers (``agent.default`` / ``agent.<agent>``); every
    other scope is itself.  Keeps the display from printing the bare
    ``agent.bindings.*`` form, which is not a key (spec §0).
    """
    from kanibako.settings.keystore import KeyStore

    if scope != "agent":
        yield scope_node, scope
        return
    for tier in sorted(dict.keys(scope_node)):
        tier_node = dict.__getitem__(scope_node, tier)
        if isinstance(tier_node, KeyStore):
            yield tier_node, f"agent.{tier}"


def _sub(node: Any, path: "tuple[str, ...]") -> Any:
    """Walk *path* under *node* with unbound ``dict`` ops (S3); ``None`` if absent."""
    from kanibako.settings.keystore import KeyStore

    cur: Any = node
    for seg in path:
        if not isinstance(cur, KeyStore):
            return None
        cur = dict.get(cur, seg, None)
    return cur
