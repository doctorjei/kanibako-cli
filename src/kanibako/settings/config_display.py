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
from kanibako.settings.settings_prefs import PREF_ROOT
from kanibako.settings.settings_store import _MISSING


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
    """
    from kanibako.settings.settings_store import Bind, KeyStore

    node = snapshot
    for seg in (PREF_ROOT,):
        if not isinstance(node, KeyStore):
            return
        node = dict.get(node, seg, None)
    if not isinstance(node, KeyStore):
        return

    requests: dict[str, Any] = {}

    def _walk(sub: Any, prefix: str) -> None:
        for k in dict.keys(sub):
            v = dict.__getitem__(sub, k)
            if isinstance(v, KeyStore):
                _walk(v, f"{prefix}{k}.")
            else:
                requests[f"{prefix}{k}"] = v

    _walk(node, "")
    if not requests:
        return

    def _render(value: Any) -> str:
        if isinstance(value, Bind):
            opts = f"  [{value.opts}]" if value.opts else ""
            return f"{value.host} -> {value.box}{opts}"
        if value is None:
            return "null"
        return str(value)

    print("", file=out)
    for target in sorted(requests):
        print(f"  {PREF_ROOT}.{target} = {_render(requests[target])}", file=out)
        # The RESULT, read at the TARGET key in the same snapshot.
        cur: Any = snapshot
        missing = False
        for seg in target.split("."):
            if not isinstance(cur, KeyStore) or dict.get(cur, seg, _MISSING) is _MISSING:
                missing = True
                break
            cur = dict.get(cur, seg)
        if missing:
            # The ordinary present-None rule OMITTED it: a bind / category /
            # masks leaf was suppressed. Saying so is the whole point — this is
            # the difference between "suppressed" and "unset". Name the CURE
            # too (B-6): suppression has no verb of its own, so the only place a
            # user learns that ``reset`` undoes it is a message like this one.
            #
            # ⚑ "at the scope that set it" is not vagueness — it is the only
            # honest form available here. Both halves of this block are read off
            # the MERGED snapshot, which no longer carries which file wrote the
            # request, and a ``reset`` issued at the wrong noun removes nothing
            # (or is refused by the directional guard). Naming a specific scope
            # would be a guess dressed as an instruction.
            result = (
                f"(omitted — the entry is suppressed; no mount. Undo with "
                f"'reset {PREF_ROOT}.{target}' at the scope that set it)"
            )
        elif cur is None:
            result = "(unset — the consumer applies its default)"
        else:
            result = _render(cur)
        print(f"    -> {target} = {result}", file=out)


def _print_category_block(snapshot: Any, error: str | None, out: Any) -> None:
    """Render the ``--effective`` PATH-DELIVERY block (spec §0; box scope, D6).

    Every CONCRETE binding is listed with the destination it occupies, and every
    ABSTRACT declaration (``common`` / ``caches`` / ``seeded``) is listed with the
    ``binding_derivations.<declaration-key>`` binding it produces indented
    beneath it — so a reader can see the declaration AND the mount it becomes,
    which is the whole point of materialising the derivation.

    Both halves are read off the SAME snapshot: the declaration at its own key,
    the derivation at ``binding_derivations.<that key>`` (the reserved internal
    node, R-8 — not a key).  Nothing is re-derived here.
    """
    from kanibako.settings.settings_store import Bind, KeyStore
    from kanibako.settings.settings_views import derived_bindings

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

    def _pair(bind: Bind) -> str:
        opts = f"  [{bind.opts}]" if bind.opts else ""
        return f"{bind.host} -> {bind.box}{opts}"

    # CONCRETE first — the source of truth a mount is emitted from.
    for scope in ("system", "agent", "workset", "box"):
        scope_node = _leaf(scope)
        if not isinstance(scope_node, KeyStore):
            continue
        for tier, prefix in _iter_agent_tiers(scope, scope_node):
            for mode in ("ro", "rw"):
                mode_node = _sub(tier, ("bindings", mode))
                if not isinstance(mode_node, KeyStore):
                    continue
                for name in sorted(dict.keys(mode_node)):
                    bind = dict.__getitem__(mode_node, name)
                    if isinstance(bind, Bind):
                        print(
                            f"  {prefix}.bindings.{mode}.{name} = {_pair(bind)}",
                            file=out,
                        )

    # ABSTRACT declarations, each with the binding it derives.
    #
    # ⚑ The derivation line carries its DELIVERY. ``seeded`` derives a COPY, not
    # a mount (spec §0), and the two are not interchangeable at all: a mount is
    # live and shadows the dest, while a copy runs once at create and is then the
    # box's own file. A reader who cannot tell them apart cannot answer the
    # question this display exists for — WHY is this here, and what happens if I
    # change it.
    derived_node = _leaf("binding_derivations")
    if isinstance(derived_node, KeyStore):
        for decl_key, bind in sorted(derived_bindings(derived_node).items()):
            declaration = _leaf(decl_key)
            if isinstance(declaration, Bind):
                print(f"  {decl_key} = {_pair(declaration)}", file=out)
            kind = "copy" if _declaration_delivery(decl_key) == "COPY" else "mount"
            print(
                f"    binding_derivations.{decl_key} = {_pair(bind)}  ({kind})",
                file=out,
            )


def _declaration_delivery(decl_key: str) -> str:
    """The COPY/MOUNT delivery of a declaration key, from the category table.

    The category is the segment after the scope, and the AGENT scope is
    DISCRIMINATED — two segments (``agent.<tier>``) where every other scope is
    one. Parsed by position rather than by substring search, so a name that
    happens to spell a category (``box.caches.common``) cannot be misread.

    The delivery itself is read off ``settings_categories._DELIVERY``: it has ONE
    definition, and a display keeping its own copy would drift the moment a
    category moved between COPY and MOUNT.
    """
    from kanibako.settings.settings_categories import _DELIVERY

    parts = decl_key.split(".")
    idx = 2 if parts[0] == "agent" else 1
    category = parts[idx] if len(parts) > idx else ""
    return _DELIVERY.get(category, "MOUNT")


def _iter_agent_tiers(scope: str, scope_node: Any):
    """``(node, key-prefix)`` per DISCRIMINATED tier of *scope*.

    Only the agent scope has tiers (``agent.default`` / ``agent.<agent>``); every
    other scope is itself.  Keeps the display from printing the bare
    ``agent.bindings.*`` form, which is not a key (spec §0).
    """
    from kanibako.settings.settings_store import KeyStore

    if scope != "agent":
        yield scope_node, scope
        return
    for tier in sorted(dict.keys(scope_node)):
        tier_node = dict.__getitem__(scope_node, tier)
        if isinstance(tier_node, KeyStore):
            yield tier_node, f"agent.{tier}"


def _sub(node: Any, path: "tuple[str, ...]") -> Any:
    """Walk *path* under *node* with unbound ``dict`` ops (S3); ``None`` if absent."""
    from kanibako.settings.settings_store import KeyStore

    cur: Any = node
    for seg in path:
        if not isinstance(cur, KeyStore):
            return None
        cur = dict.get(cur, seg, None)
    return cur
