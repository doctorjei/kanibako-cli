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
from typing import Any

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


def _print_category_block(snapshot: Any, error: str | None, out: Any) -> None:
    """Render the ``--effective`` PATH-DELIVERY block (spec §0; box scope, D6).

    The pid-0 FOUNDATION comes first, then every CONCRETE binding with the
    destination it occupies — all read off the SAME snapshot the launch resolved.
    Nothing is re-derived here.

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
        effective_bindings_and_template_sources,
    )
    from kanibako.settings.settings_launch import BOX_HOME_KEY
    from kanibako.settings.store_collapse import HOME_DEST

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

    # CONCRETE next — the source of truth a mount is emitted from.
    #
    # ⚑ The arm is DEST-KEYED (R-5/R-6): the map KEY is the box destination and
    # the leaf is a 2-element ``BindEntry(src, opts)`` carrying no destination at
    # all, so the pair is assembled from the key and the leaf TOGETHER. The leaf
    # test is ``isinstance`` and never arity — a legacy 3-tuple ``Bind`` and a
    # ``BindEntry`` are both legally 2 elements with OPPOSITE meanings
    # (``kb_store.BindEntry``, the arity trap). A leaf that is neither is a
    # malformed arm the launch already refused, which is why the display is
    # showing *error* instead of reaching here.
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
                        opts = f"  [{entry.opts}]" if entry.opts else ""
                        print(
                            f"  {prefix}.bindings.{mode}.{dest} = "
                            f"{entry.src} -> {dest}{opts}",
                            file=out,
                        )

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
    for row in effective_bindings_and_template_sources(snapshot):
        print(f"  {row.declaration.key} = {row.declaration.src}", file=out)
        print(
            f"    {BINDING_DERIVATIONS_NODE}.{row.declaration.key} = "
            f"{_derivation_result(row)}",
            file=out,
        )


def _derivation_result(row: Any) -> str:
    """One :class:`~kanibako.settings.store_collapse.Derivation` as its result phrase.

    ⚑ EVERY OUTCOME PRINTS SOMETHING, and a LOSS prints WHY and WHERE. "No mount"
    on its own is the answer a user cannot act on; the destination that swallowed
    the declaration is the whole diagnosis, and for a mask ABOVE the declaration it
    is not a destination the user's own key names.
    """
    from kanibako.settings.store_collapse import (
        DERIVED_AMBIGUOUS,
        DERIVED_COPY,
        DERIVED_MASKED,
        DERIVED_MOUNT,
        DERIVED_SUPERSEDED,
    )

    if row.outcome == DERIVED_COPY:
        return f"{row.copy.src} -> {row.copy.dest}  (copy)"
    if row.outcome == DERIVED_MASKED:
        return (
            f"(no mount — the mask at {row.at} covers this destination, and a mask "
            f"has no host source: the box sees nothing at that path)"
        )
    if row.outcome == DERIVED_SUPERSEDED:
        if row.bind is None:
            return (
                "(no copy — no collapsed copy row accounts for this declaration; "
                "another declaration at this destination took it)"
            )
        return (
            f"(no mount — the binding of {row.bind.src} at {row.at} occupies this "
            f"destination)"
        )
    if row.outcome == DERIVED_AMBIGUOUS:
        return (
            f"{row.bind.src} -> {row.at}  [{row.bind.opts}]  (mount — AMBIGUOUS: "
            f"another declaration at this destination names the same source, and "
            f"only one of them is this mount)"
        )
    if row.outcome == DERIVED_MOUNT:
        # ⚑ THE DELIVERY IS STATED, not left to be inferred from the presence of an
        # options column: a mount is LIVE and shadows the dest, a copy runs once and
        # is then the box's own file, and a reader who cannot tell them apart cannot
        # answer the question this display exists for (N2).
        return f"{row.bind.src} -> {row.at}  [{row.bind.opts}]  (mount)"
    # ⚑ UNCOVERED, and it is not "no mount": this resolve carries no collapsed
    # bind map at all (a NARROW resolve writes none), so the question was not
    # answered rather than answered in the negative.
    return (
        "(unknown — this resolve carries no collapsed binding map, so what the "
        "box receives here cannot be read)"
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
