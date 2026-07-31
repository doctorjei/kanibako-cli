"""Unit tests for the unified scope-category resolver + the FROZEN oracle it uses.

⚠⚠⚠  READ BEFORE COPYING ANYTHING OUT OF THIS FILE  ⚠⚠⚠

This file holds BOTH the frozen snapshot of the RETIRED by-name category resolver
(``flawed_oracle_categories``, formerly ``resolve_categories``) and the tests that
drive it. They live together so the retired model occupies exactly ONE file — it is
a drift tripwire, NOT the live route and NOT a correctness authority. Adjudicate any
divergence against the SPEC, never against this file.

Because it is frozen, it still speaks the retired UNDISCRIMINATED key shape
``agent.<category>.<name>``. **That form is not a key and does not exist anywhere in
live kanibako** — the keyspace is CLOSED (spec §0) and the agent tier is
DISCRIMINATED (§2d / §0 L21), so every real agent key is ``agent.<agent>.…`` or
``agent.default.…``, and the live patterns in ``kanibako.settings_categories``
REFUSE the bare form.

So: the ``agent.caches.x`` / ``agent.bindings.rw.x`` strings below are FROZEN LEGACY
FIXTURES, quarantined to this file. Do not copy them into new tests, into production
code, or into a config file. If you are writing a new test against the LIVE route,
write ``agent.<agent>.<category>.<name>``.
"""

from __future__ import annotations

from re import compile as _re_compile

from collections.abc import Callable, Mapping

import pytest

from kanibako.errors import CategoryCollisionError, ConfigError
from kanibako.settings_categories import (
    COPY,
    ENV,
    MOUNT,
    CategoryEntry,
    reconcile_categories,
)
from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
    unpack_bind,
)

# ─── FORKED FROM live ``kanibako.settings_categories`` — FROZEN COPIES ─────────
# These were imported from live code until 2026-07-29. A "frozen baseline" that
# imports live internals is NOT frozen: a live edit silently rewrites the thing the
# live code is being checked against, and the tripwire stops tripping. They are
# copies ON PURPOSE. If a live edit makes the equivalence test fail, that is the
# tripwire WORKING — decide deliberately whether to update these copies, do not
# reflexively re-import.
_FROZEN_COPY = "COPY"
_FROZEN_ENV = "ENV"
_FROZEN_MOUNT = "MOUNT"

_DISABLE_SENTINEL = "empty"
_SCOPE_APPLY_ORDER = {"system": 0, "agent": 1, "workset": 2, "box": 3}

_DELIVERY = {
    "masks": _FROZEN_MOUNT,
    "bindings.ro": _FROZEN_MOUNT,
    "bindings.rw": _FROZEN_MOUNT,
    "caches": _FROZEN_MOUNT,
    "seeded": _FROZEN_COPY,
    "common": _FROZEN_MOUNT,
    "synced": _FROZEN_COPY,
    "env": _FROZEN_ENV,
    "secret_path": _FROZEN_MOUNT,
}


def _bind_options(category: str) -> str:
    """FROZEN copy of the live mount-option rule (ro for bindings.ro, else Z,U)."""
    return "ro" if category == "bindings.ro" else "Z,U"

# ═══════════════════════════════════════════════════════════════════════════════
# ⚠⚠⚠  FROZEN LEGACY KEY SHAPES — DO NOT COPY, DO NOT IMPORT, DO NOT IMITATE  ⚠⚠⚠
#
# These three patterns accept the RETIRED, UNDISCRIMINATED ``agent.<category>``
# form. That form IS NOT A KEY and does not exist anywhere in live kanibako: the
# keyspace is CLOSED (spec §0) and the agent tier is DISCRIMINATED (§2d / §0 L21),
# so every real agent key is ``agent.<agent>.…`` or ``agent.default.…``.
#
# They live HERE, and only here, because this module is a FROZEN snapshot of the
# retired by-name resolver, kept as a drift tripwire. Reproducing the old model is
# its entire job. The live patterns in ``kanibako.settings_categories`` REFUSE this
# shape on purpose.
#
# If you are reading this because you want an ``agent.<category>`` key to work:
# you don't. Write ``agent.<agent>.<category>.<name>``.
# ═══════════════════════════════════════════════════════════════════════════════
_LEGACY_CATEGORY_ALT = "|".join(
    c.replace(".", r"\.")
    for c in ("bindings.ro", "bindings.rw", "caches", "seeded", "common", "synced")
)
BIND_KEY_RE = _re_compile(
    rf"^(?P<scope>system|agent|workset|box)\.(?P<category>{_LEGACY_CATEGORY_ALT})"
    r"\.(?P<name>.+)$"
)
MASK_KEY_RE = _re_compile(r"^(?P<scope>system|agent|workset|box)\.masks$")
ENV_KEY_RE = _re_compile(
    r"^(?P<scope>system|agent|workset|box)\.env\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)$"
)


def flawed_oracle_is_category_key(key: str) -> bool:
    """True if *key* is any scope-category key (one of the eight shapes)."""
    return (
        BIND_KEY_RE.match(key) is not None
        or MASK_KEY_RE.match(key) is not None
        or ENV_KEY_RE.match(key) is not None
    )


def _as_scalar(value: object) -> str:
    """Narrow a resolved ``env`` value to its scalar ``str`` form.

    The LOAD layer preserves structured category leaves (binding pair/tuple,
    ``masks`` list — spec §2a), so a resolved value is typed ``object``.  ``env``
    is the one remaining scalar category (its value is a plain VAR value); this
    narrows it to ``str`` (a real ``str`` passes through untouched).  Binding and
    ``masks`` leaves are unpacked structurally (:func:`unpack_bind` /
    :func:`_mask_dests`) and do NOT pass through here.
    """
    return value if isinstance(value, str) else str(value)


def _discover(levels: list[LevelView], pred: Callable[[str], bool]) -> set[str]:
    """Collect every key (values AND defaults, all levels) matching *pred*."""
    keys: set[str] = set()
    for level in levels:
        for key in level.values:
            if pred(key):
                keys.add(key)
        for key in level.defaults:
            if pred(key):
                keys.add(key)
    return keys


def _mask_dests(value: object) -> list[str]:
    """Narrow a resolved ``masks`` value to its real ``list[box_dest]``.

    Per spec §2a ``masks`` is a real ``list[box_dest]`` — the LOAD layer (P1)
    preserves a YAML list verbatim, so the common case is an actual ``list`` /
    ``tuple`` whose elements ARE the box-dest paths (each kept as-is, NOT
    re-derived from a flattened string — the old comma-string shim
    ``str()``-reprd a preserved list into garbage, the latent corruption this
    closes).  A bare scalar ``str`` (a single-mask config or in-code default) is
    a one-element list.  Empty / whitespace-only elements are dropped.
    """
    raw = value if isinstance(value, (list, tuple)) else [value]
    return [s for s in (str(e).strip() for e in raw) if s]


def flawed_oracle_categories(
    *,
    levels: list[LevelView],
    ctx: ResolveCtx,
    lookup: Callable[[str, tuple[str, ...]], str],
    scope_roots: Mapping[str, str] | None = None,
) -> list[CategoryEntry]:
    """Resolve the unified scope-category config into ordered entries.

    .. note::
       **FROZEN LEGACY BASELINE — NO product caller.** This is the OLD by-NAME
       LevelView-cascade resolver, RETIRED because it was WRONG in a number of
       cases.  The launch + CLI paths now resolve categories through the KeyStore
       snapshot pipeline (``build_launch_snapshot`` →
       ``snapshot_category_entries`` → :func:`reconcile_categories`).  This
       function is kept SOLELY as the drift TRIPWIRE that
       ``tests/test_settings_launch_equivalence.py`` compares the snapshot path
       against.  It is NOT a correctness authority: on a divergence the SPEC
       (``reference/settings-keyspace-1.6.0-target.md``) adjudicates, never this
       code (the OLD path here could be the buggy side).

    *levels* are MOST-SPECIFIC-FIRST (``[box, workset, agent, system, ...]``).
    *lookup* resolves ``@``-refs.  *scope_roots* maps a group prefix
    (``"{scope}.<category>"``) to a host-space root expression; absent/empty
    means no root join (bind-shaped categories only).

    Returns entries in apply order (see :mod:`kanibako.settings_categories`
    module docstring).  Does NOT resolve cross-category collisions (sub-step 4b).
    Raises :class:`SettingsError` if a non-suppressed bind value is not a
    structured 2-/3-element pair/tuple
    (:func:`~kanibako.settings_resolve.unpack_bind`).
    """
    entries: list[tuple[tuple[int, str, str], CategoryEntry]] = []

    # --- bind-shaped categories: bindings.{ro,rw} / caches / seeded / shared / synced
    for key in _discover(levels, lambda k: BIND_KEY_RE.match(k) is not None):
        m = BIND_KEY_RE.match(key)
        assert m is not None
        scope = m.group("scope")
        category = m.group("category")
        name = m.group("name")
        group = f"{scope}.{category}"
        delivery = _DELIVERY[category]

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            continue
        if rv.terminal:
            # Explicit "" — suppressed.
            continue
        if delivery == _FROZEN_COPY and rv.value == _DISABLE_SENTINEL:
            # "empty" sentinel disables a COPY (seed/synced) entry.
            continue

        # Structured unpack (spec §2a): a category binding value is a 2-/3-element
        # list/tuple, NOT a colon-string. The optional 3rd element is the
        # per-entry options override (``opts_override``): when PRESENT (a 3-element
        # entry — incl. an explicit empty string ``""``) it OVERRIDES the category
        # default for THIS entry; when absent (``None``, a 2-element entry) the
        # category default applies.
        try:
            host_src_raw, guest_dest_raw, opts_override = unpack_bind(rv.value)
        except SettingsError as exc:
            raise SettingsError(f"Category '{key}': {exc}") from exc

        host_src = expand_expr(host_src_raw, space="host", ctx=ctx, lookup=lookup)
        guest_dest = expand_expr(guest_dest_raw, space="guest", ctx=ctx, lookup=lookup)

        # Root join: only for a relative host_src under a group that has a root.
        root_expr = scope_roots.get(group) if scope_roots else None
        if root_expr and not host_src.startswith("/"):
            root = expand_expr(root_expr, space="host", ctx=ctx, lookup=lookup)
            host_src = f"{root.rstrip('/')}/{host_src}"

        # Per-entry options override (spec §2a): an explicit 3rd element wins for
        # this MOUNT entry (incl. an explicit ``""`` — no relabel); a 2-element
        # entry (``opts_override is None``) falls back to the category default.
        # COPY / ENV deliveries carry no mount flags (options stays ``""``).
        if delivery == _FROZEN_MOUNT:
            options = opts_override if opts_override is not None else _bind_options(category)
        else:
            options = ""
        sort_key = (_SCOPE_APPLY_ORDER[scope], category, name)
        entries.append(
            (
                sort_key,
                CategoryEntry(
                    category=category,
                    scope=scope,
                    box_dest=guest_dest,
                    host_src=host_src,
                    delivery=delivery,
                    options=options,
                    name=name,
                    key=f"{scope}.{category}.{name}",
                ),
            )
        )

    # --- masks: {scope}.masks = list[box_dest] (tmpfs hide; no host source)
    for key in _discover(levels, lambda k: MASK_KEY_RE.match(k) is not None):
        m = MASK_KEY_RE.match(key)
        assert m is not None
        scope = m.group("scope")

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset) or rv.terminal:
            continue
        for idx, raw_dest in enumerate(_mask_dests(rv.value)):
            box_dest = expand_expr(raw_dest, space="guest", ctx=ctx, lookup=lookup)
            sort_key = (_SCOPE_APPLY_ORDER[scope], "masks", f"{idx:04d}:{box_dest}")
            entries.append(
                (
                    sort_key,
                    CategoryEntry(
                        category="masks",
                        scope=scope,
                        box_dest=box_dest,
                        host_src=None,
                        delivery=_FROZEN_MOUNT,
                        options="ro",
                        name=box_dest,
                        key=f"{scope}.masks.{raw_dest}",
                    ),
                )
            )

    # --- env: {scope}.env.{VAR} = value (scalar; no host source, no guest path)
    for key in _discover(levels, lambda k: ENV_KEY_RE.match(k) is not None):
        m = ENV_KEY_RE.match(key)
        assert m is not None
        scope = m.group("scope")
        var = m.group("name")

        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset) or rv.terminal:
            continue
        value = expand_expr(_as_scalar(rv.value), space="guest", ctx=ctx, lookup=lookup)
        sort_key = (_SCOPE_APPLY_ORDER[scope], "env", var)
        entries.append(
            (
                sort_key,
                CategoryEntry(
                    category="env",
                    scope=scope,
                    box_dest=var,
                    host_src=None,
                    delivery=_FROZEN_ENV,
                    options=value,
                    name=var,
                    key=f"{scope}.env.{var}",
                ),
            )
        )

    entries.sort(key=lambda pair: pair[0])
    return [entry for _, entry in entries]


HOST_HOME = "/home/u"


def make_ctx(
    *,
    agent_name: str | None = "claude",
    workset_name: str | None = "myws",
    host_home: str = HOST_HOME,
    xdg: dict[str, str] | None = None,
) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home,
        xdg=xdg if xdg is not None else {"XDG_DATA_HOME": "/data"},
    )


def make_lookup(levels: list[LevelView], ctx: ResolveCtx):
    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(f"Unknown @-reference: {ref}")
        return expand_expr(rv.value, space="host", ctx=ctx, lookup=lookup, chain=chain)

    return lookup


def _resolve(levels, ctx, scope_roots=None) -> list[CategoryEntry]:
    lookup = make_lookup(levels, ctx)
    return flawed_oracle_categories(
        levels=levels, ctx=ctx, lookup=lookup, scope_roots=scope_roots
    )


def _one(levels, ctx, **kw) -> CategoryEntry:
    entries = _resolve(levels, ctx, **kw)
    assert len(entries) == 1, entries
    return entries[0]


# ---------------------------------------------------------------------------
# Each category parses to the right CategoryEntry shape + delivery tag.
# ---------------------------------------------------------------------------


class TestEachCategoryShape:
    def test_bindings_ro_is_mount_ro(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("box", {"box.bindings.ro.docs": ["/h/docs", "/g/docs"]})], ctx
        )
        assert e.category == "bindings.ro"
        assert e.scope == "box"
        assert e.host_src == "/h/docs"
        assert e.box_dest == "/g/docs"
        assert e.delivery == MOUNT
        assert e.options == "ro"
        assert e.name == "docs"

    def test_bindings_rw_is_mount_zu(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.work": ["/h/w", "~/w"]})], ctx)
        assert e.category == "bindings.rw"
        assert e.host_src == "/h/w"
        assert e.box_dest == "/home/agent/w"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"

    def test_caches_is_mount_zu(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("agent", {"agent.caches.tweak": ["/h/c", "~/.cache/x"]})], ctx
        )
        assert e.category == "caches"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"
        assert e.host_src == "/h/c"
        assert e.box_dest == "/home/agent/.cache/x"

    def test_shared_is_mount_zu(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("workset", {"workset.common.team": ["/h/s", "~/shared"]})], ctx
        )
        assert e.category == "common"
        assert e.delivery == MOUNT
        assert e.options == "Z,U"

    def test_seeded_is_copy(self):
        ctx = make_ctx()
        e = _one([LevelView("agent", {"agent.seeded.shell": ["/tmpl", "~/"]})], ctx)
        assert e.category == "seeded"
        assert e.delivery == COPY
        assert e.options == ""
        assert e.host_src == "/tmpl"
        assert e.box_dest == "/home/agent/"

    def test_synced_is_copy(self):
        ctx = make_ctx()
        e = _one(
            [LevelView("agent", {"agent.synced.creds": ["~/.claude/c", "~/.claude/c"]})],
            ctx,
        )
        assert e.category == "synced"
        assert e.delivery == COPY
        assert e.options == ""
        assert e.host_src == "/home/u/.claude/c"
        assert e.box_dest == "/home/agent/.claude/c"

    def test_masks_is_mount_no_source(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.masks": "~/workspace/vault"})], ctx)
        assert e.category == "masks"
        assert e.delivery == MOUNT
        assert e.host_src is None
        assert e.box_dest == "/home/agent/workspace/vault"
        assert e.options == "ro"

    def test_env_is_env_value_in_options(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.env.FOO": "bar"})], ctx)
        assert e.category == "env"
        assert e.delivery == ENV
        assert e.host_src is None
        assert e.box_dest == "FOO"   # the VAR name
        assert e.options == "bar"    # the VALUE
        assert e.name == "FOO"


# ---------------------------------------------------------------------------
# Structured representation (spec §2a): bind values are 2-/3-element pairs/tuples,
# never colon-strings. The 3rd element is the per-entry options override.
# ---------------------------------------------------------------------------


class TestStructuredRepresentation:
    def test_two_tuple_unpacks_host_and_dest(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.home": ["/host/home", "~/"]})], ctx)
        assert e.host_src == "/host/home"
        assert e.box_dest == "/home/agent/"

    def test_two_tuple_as_python_tuple(self):
        # A Python tuple leaf (as well as a YAML list) unpacks identically.
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.x": ("/h/x", "/g/x")})], ctx)
        assert e.host_src == "/h/x"
        assert e.box_dest == "/g/x"

    def test_three_tuple_third_element_is_captured(self):
        # The optional 3rd element (per-entry mount-options override) is captured
        # by the structural unpacker. P2 captures it (no crash on the 3-element
        # form); P3 threads it into the entry's mount options.
        from kanibako.settings_resolve import unpack_bind

        assert unpack_bind(["/h/sock", "~/helper.sock", "z"]) == (
            "/h/sock",
            "~/helper.sock",
            "z",
        )
        assert unpack_bind(("/h/sock", "~/helper.sock", "")) == (
            "/h/sock",
            "~/helper.sock",
            "",
        )

    def test_three_tuple_resolves_through_category_path(self):
        # A 3-element binding value resolves end-to-end (host/dest expanded) and the
        # explicit options slot OVERRIDES the category default (P3 threading).
        ctx = make_ctx()
        e = _one(
            [LevelView("box", {"box.bindings.rw.helper_sock": ["/h/s", "~/helper.sock", "z"]})],
            ctx,
        )
        assert e.host_src == "/h/s"
        assert e.box_dest == "/home/agent/helper.sock"
        assert e.options == "z"  # explicit override beats the rw default Z,U


# ---------------------------------------------------------------------------
# P3: the per-entry options override (3rd element) threads to CategoryEntry.options.
# ---------------------------------------------------------------------------


class TestPerEntryOptionsOverride:
    def test_explicit_override_wins_over_rw_default(self):
        # [host, dest, "z"] -> options == "z" (not the rw default "Z,U").
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.s": ["/h/s", "/g/s", "z"]})], ctx)
        assert e.options == "z"

    def test_explicit_override_wins_over_ro_default(self):
        # An explicit override beats the ro default "ro" too.
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.s": ["/h/s", "/g/s", "rw,Z"]})], ctx)
        assert e.options == "rw,Z"

    def test_explicit_empty_override_means_no_relabel(self):
        # [host, dest, ""] resolves to "" — an explicit empty options (live socket:
        # no Z,U relabel), DISTINCT from the 2-element default-fallback case.
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.sock": ["/h/s", "/g/s", ""]})], ctx)
        assert e.options == ""

    def test_two_element_falls_back_to_rw_default(self):
        # A 2-element rw entry (no override) keeps the category default "Z,U".
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.s": ["/h/s", "/g/s"]})], ctx)
        assert e.options == "Z,U"

    def test_two_element_falls_back_to_ro_default(self):
        # A 2-element ro entry (no override) keeps the category default "ro".
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.s": ["/h/s", "/g/s"]})], ctx)
        assert e.options == "ro"

    def test_override_on_caches_and_shared(self):
        # The override channel is per-MOUNT-category, not just bindings.
        ctx = make_ctx()
        ec = _one([LevelView("box", {"box.caches.c": ["/h/c", "/g/c", "U"]})], ctx)
        assert ec.options == "U"
        es = _one([LevelView("box", {"box.common.s": ["/h/s", "/g/s", ""]})], ctx)
        assert es.options == ""

    def test_copy_category_ignores_options_slot(self):
        # COPY deliveries (seeded/synced) carry no mount flags: options stays ""
        # even when a 3rd element is present (it is not a mount option there).
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.seeded.s": ["/h/s", "/g/s", "z"]})], ctx)
        assert e.delivery == COPY
        assert e.options == ""

    def test_two_tuple_unpack_returns_none_for_options(self):
        from kanibako.settings_resolve import unpack_bind

        assert unpack_bind(["/h", "/g"]) == ("/h", "/g", None)

    def test_non_structured_value_raises(self):
        # A bare scalar (the old colon-string form) is no longer a valid category
        # binding value — the structured pair/tuple is load-bearing.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.bad": "/just/a/path"})]
        with pytest.raises(SettingsError):
            _resolve(levels, ctx)

    def test_wrong_arity_raises(self):
        from kanibako.settings_resolve import unpack_bind

        with pytest.raises(SettingsError):
            unpack_bind(["only-one"])
        with pytest.raises(SettingsError):
            unpack_bind(["a", "b", "c", "d"])


# ---------------------------------------------------------------------------
# masks list shape
# ---------------------------------------------------------------------------


class TestMasks:
    def test_multiple_masks_from_real_list(self):
        # F1 regression: a multi-element YAML masks list (spec §2a — a real
        # list[box_dest], preserved by the LOAD layer) resolves to ONE entry
        # per element, NOT a single str()-reprd-garbage entry.
        ctx = make_ctx()
        entries = _resolve(
            [LevelView("box", {"box.masks": ["~/workspace/vault", "/secret", "~/cache"]})],
            ctx,
        )
        assert [e.box_dest for e in entries] == [
            "/home/agent/workspace/vault",
            "/secret",
            "/home/agent/cache",
        ]
        assert all(e.category == "masks" and e.host_src is None for e in entries)

    def test_single_mask_scalar_is_one_entry(self):
        # A bare scalar (single-mask config / in-code default) = one element.
        ctx = make_ctx()
        entries = _resolve([LevelView("box", {"box.masks": "/a,b"})], ctx)
        assert [e.box_dest for e in entries] == ["/a,b"]

    def test_mask_list_element_with_literal_comma_kept(self):
        # A literal comma inside a list element is NOT a separator (no shim).
        ctx = make_ctx()
        entries = _resolve([LevelView("box", {"box.masks": ["/a,b", "/c"]})], ctx)
        assert [e.box_dest for e in entries] == ["/a,b", "/c"]

    def test_mask_terminal_suppression(self):
        ctx = make_ctx()
        entries = _resolve(
            [
                LevelView("box", {"system.masks": ""}),
                LevelView("system", {"system.masks": "~/x"}),
            ],
            ctx,
        )
        assert entries == []


# ---------------------------------------------------------------------------
# env value shape
# ---------------------------------------------------------------------------


class TestEnv:
    def test_env_expands_value(self):
        ctx = make_ctx(agent_name="claude")
        e = _one([LevelView("box", {"box.env.HOME_AGENT": "~/.local"})], ctx)
        assert e.box_dest == "HOME_AGENT"
        assert e.options == "/home/agent/.local"

    def test_env_dotted_var_not_matched(self):
        # env VAR names cannot contain dots, so a dotted "VAR" is not an env key.
        assert not flawed_oracle_is_category_key("box.env.A.B")

    def test_env_precedence_box_over_system(self):
        ctx = make_ctx()
        e = _one(
            [
                LevelView("box", {"system.env.K": "box"}),
                LevelView("system", {"system.env.K": "sys"}),
            ],
            ctx,
        )
        assert e.options == "box"


# ---------------------------------------------------------------------------
# COPY vs MOUNT delivery tagging across the categories
# ---------------------------------------------------------------------------


class TestDeliveryTagging:
    def test_copy_set_vs_mount_set(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.seeded.s": ["/h/s", "~/s"],
                    "box.synced.y": ["/h/y", "~/y"],
                    "box.bindings.rw.b": ["/h/b", "~/b"],
                    "box.caches.c": ["/h/c", "~/c"],
                    "box.common.h": ["/h/h", "~/h"],
                },
            ),
        ]
        by_cat = {e.category: e.delivery for e in _resolve(levels, ctx)}
        assert by_cat["seeded"] == COPY
        assert by_cat["synced"] == COPY
        assert by_cat["bindings.rw"] == MOUNT
        assert by_cat["caches"] == MOUNT
        assert by_cat["common"] == MOUNT


# ---------------------------------------------------------------------------
# Scope apply order (system -> agent -> workset -> box, box LAST)
# ---------------------------------------------------------------------------


class TestScopeApplyOrder:
    def test_distinct_scopes_apply_order(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.bindings.rw.b": ["/hb", "/gb"]}),
            LevelView("workset", {"workset.bindings.rw.w": ["/hw", "/gw"]}),
            LevelView("agent", {"agent.bindings.rw.a": ["/ha", "/ga"]}),
            LevelView("system", {"system.bindings.rw.s": ["/hs", "/gs"]}),
        ]
        dests = [e.box_dest for e in _resolve(levels, ctx)]
        assert dests == ["/gs", "/ga", "/gw", "/gb"]

    def test_within_scope_ordered_by_category_then_name(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.bindings.rw.z": ["/hz", "/gz"],
                    "box.bindings.rw.a": ["/ha", "/ga"],
                    "box.bindings.ro.m": ["/hm", "/gm"],
                    "box.caches.k": ["/hk", "/gk"],
                },
            ),
        ]
        # bindings.ro < bindings.rw < caches (category asc); within → name asc.
        dests = [e.box_dest for e in _resolve(levels, ctx)]
        assert dests == ["/gm", "/ga", "/gz", "/gk"]


# ---------------------------------------------------------------------------
# Precedence / suppression / discovery (engine semantics, generalized)
# ---------------------------------------------------------------------------


class TestPrecedenceAndSuppression:
    def test_same_key_most_specific_wins(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.bindings.rw.foo": ["/box", "/g"]}),
            LevelView("system", {"system.bindings.rw.foo": ["/sys", "/g"]}),
        ]
        assert _one(levels, ctx).host_src == "/box"

    def test_terminal_empty_suppresses_binding(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"system.bindings.rw.foo": ""}),
            LevelView(
                "system",
                {
                    "system.bindings.rw.foo": ["/sf", "/gf"],
                    "system.bindings.rw.bar": ["/sb", "/gb"],
                },
            ),
        ]
        assert [e.box_dest for e in _resolve(levels, ctx)] == ["/gb"]

    def test_empty_sentinel_disables_copy(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {"box.seeded.x": "empty"}),
            LevelView("box", {"box.seeded.y": ["/hy", "/gy"]}),
        ]
        # box.seeded.x is disabled; only y survives.
        names = [e.name for e in _resolve(levels, ctx)]
        assert names == ["y"]

    def test_empty_sentinel_does_NOT_disable_mount(self):
        # "empty" is only a sentinel for COPY categories; a binding (MOUNT) value
        # of "empty" is a non-structured scalar -> not a valid pair/tuple -> error.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.x": "empty"})]
        with pytest.raises(SettingsError):
            _resolve(levels, ctx)

    def test_default_only_category_is_discovered(self):
        ctx = make_ctx()
        levels = [
            LevelView("box", {}),
            LevelView(
                "agent", {}, defaults={"agent.bindings.ro.cfg": ["/h/cfg", "/g/cfg"]}
            ),
        ]
        e = _one(levels, ctx)
        assert e.host_src == "/h/cfg"
        assert e.options == "ro"


# ---------------------------------------------------------------------------
# Root join (bind-shaped categories)
# ---------------------------------------------------------------------------


class TestRootJoin:
    """⚑ DELETED BEHAVIOUR — these guard NOTHING in the product (P3, 2026-07-31).

    The assembly-time root-prepend they exercise lives ONLY in this file's frozen
    copy of the retired by-name resolver. The live path no longer joins anything:
    sources are rooted at DECLARATION and a stored source resolves on its own
    (spec §2a L474-486, which names the mechanism and requires its deletion).

    They stay because this whole file is a QUARANTINED frozen baseline — deleting
    parts of a frozen artefact defeats its purpose — but do NOT read them as a
    statement about how kanibako resolves a source, and do NOT copy the shape.
    """

    def test_relative_host_src_joined_under_group_root(self):
        ctx = make_ctx(agent_name="claude")
        levels = [
            LevelView("agent", {"agent.bindings.rw.plugins": ["plugins", "~/.claude/plugins"]}),
            LevelView("system", {}, defaults={"system.agents": "/data/agents"}),
        ]
        scope_roots = {"agent.bindings.rw": "@system.agents/$AGENT/share"}
        e = _one(levels, ctx, scope_roots=scope_roots)
        assert e.host_src == "/data/agents/claude/share/plugins"

    def test_absolute_host_src_not_joined(self):
        ctx = make_ctx(agent_name="claude")
        levels = [LevelView("agent", {"agent.bindings.rw.x": ["/abs", "~/x"]})]
        scope_roots = {"agent.bindings.rw": "/root"}
        assert _one(levels, ctx, scope_roots=scope_roots).host_src == "/abs"

    def test_caches_group_root(self):
        ctx = make_ctx()
        levels = [LevelView("agent", {"agent.caches.c": ["rel", "~/c"]})]
        e = _one(levels, ctx, scope_roots={"agent.caches": "/croot"})
        assert e.host_src == "/croot/rel"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class TestErrors:
    def test_non_structured_bind_raises_naming_key(self):
        # A bare scalar (the old colon-string form) is no longer a valid binding
        # value; the wrapped error names the offending category key.
        ctx = make_ctx()
        levels = [LevelView("box", {"box.bindings.rw.bad": "/just/a/path"})]
        with pytest.raises(SettingsError) as exc:
            _resolve(levels, ctx)
        assert "box.bindings.rw.bad" in str(exc.value)

    def test_literal_colon_in_path_needs_no_escaping(self):
        # In the structured form a path with a literal ':' is just a list
        # element — no colon-escaping (the structured shape has no delimiter).
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.rw.c": ["/a:b", "/g"]})], ctx)
        assert e.host_src == "/a:b"
        assert e.box_dest == "/g"

    def test_name_with_dots(self):
        ctx = make_ctx()
        e = _one([LevelView("box", {"box.bindings.ro.a.b.c": ["/h", "/g"]})], ctx)
        assert e.name == "a.b.c"


# ---------------------------------------------------------------------------
# flawed_oracle_is_category_key
# ---------------------------------------------------------------------------


class TestIsCategoryKey:
    def test_true_for_each_category(self):
        assert flawed_oracle_is_category_key("system.masks")
        assert flawed_oracle_is_category_key("box.bindings.ro.x")
        assert flawed_oracle_is_category_key("box.bindings.rw.x")
        assert flawed_oracle_is_category_key("agent.caches.k")
        assert flawed_oracle_is_category_key("agent.seeded.t")
        assert flawed_oracle_is_category_key("workset.common.s")
        assert flawed_oracle_is_category_key("agent.synced.c")
        assert flawed_oracle_is_category_key("box.env.FOO")
        # Dotted name allowed for bind categories.
        assert flawed_oracle_is_category_key("system.bindings.rw.a.b.c")

    def test_false_for_non_category_keys(self):
        assert not flawed_oracle_is_category_key("system.data")
        assert not flawed_oracle_is_category_key("agent.model")
        assert not flawed_oracle_is_category_key("box.image")
        assert not flawed_oracle_is_category_key("nope.bindings.rw.x")
        assert not flawed_oracle_is_category_key("system.path.share_rw.foo")  # old shape gone
        assert not flawed_oracle_is_category_key("system.bindings.rw")        # missing name
        assert not flawed_oracle_is_category_key("box.bindings.xx.y")         # bad mode
        assert not flawed_oracle_is_category_key("box.env")                   # missing VAR


# ---------------------------------------------------------------------------
# 4b — collision resolver (reconcile_categories)
#
# ⚑ These drive the LIVE ``reconcile_categories``. They live in this quarantined
# file for history, not because they are frozen: the frozen thing here is
# ``flawed_oracle_categories``, and moving these out would silently relocate the
# drift tripwire. The spec §0 TABLE's own cases are in
# ``tests/test_category_collisions.py``.
#
# The flat authority ladder (seed < cache < binding < common < synced < masks)
# was DELETED in favour of the §0 table: two concrete declarations at one dest
# ERROR; a mask OVERRIDES; an abstraction extending onto an occupied dest ERRORs;
# abstraction-vs-abstraction is decided by scope, silently across scopes and with
# a WARN within one. Unchanged: synced (COPY) vs binding (MOUNT) at one dest ->
# ConfigError; MOUNTs emit depth-sorted (shallow first); deliver_creds=False
# suppresses synced + cred seeds.
# ---------------------------------------------------------------------------


def _reconcile(levels, ctx, *, deliver_creds=True, scope_roots=None):
    return reconcile_categories(
        _resolve(levels, ctx, scope_roots=scope_roots), deliver_creds=deliver_creds
    )


def _entry(
    category, *, box_dest, scope="box", host_src="/h", is_credential=False,
    name=None,
):
    """Build a CategoryEntry directly (bypasses parsing) for precise collisions.

    *name* defaults to *box_dest* (the historical shape). Pass it explicitly when
    a case needs two DISTINCT declarations at one dest — under the spec §0
    collision table the declaration KEY is what the outcome and the message are
    stated in terms of, so two entries must not share one key.
    """
    from kanibako.settings_categories import _DELIVERY, _bind_options

    delivery = _DELIVERY[category]
    host = None if category in ("masks", "env") else host_src
    options = _bind_options(category) if delivery == MOUNT else ""
    leaf = box_dest if name is None else name
    return CategoryEntry(
        category=category,
        scope=scope,
        box_dest=box_dest,
        host_src=host,
        delivery=delivery,
        options=options,
        name=leaf,
        key=f"{scope}.{category}.{leaf}",
        is_credential=is_credential,
    )


class TestReconcileCollisionTable:
    def test_identical_dest_outcomes_follow_the_collision_table(self):
        # RETIRED: the flat ladder ``seed < cache < binding < common < synced <
        # masks`` resolved every rung silently. Under the spec §0 table, three of
        # the six rungs below are now ERRORS (an abstraction meeting the concrete
        # layer) and the rest are decided by rules that are not a total order.
        D = "/g/x"
        # Every stack that mixes the CONCRETE layer with an ABSTRACTION is row 3.
        for rest in (
            ["common", "bindings.rw", "caches", "seeded"],
            ["bindings.rw", "caches", "seeded"],
        ):
            with pytest.raises(CategoryCollisionError) as exc:
                reconcile_categories([_entry(c, box_dest=D) for c in rest])
            assert exc.value.kind == "extension_onto_occupied"
        # masks OVERRIDES an all-abstract + synced pile (row 2 + the unchanged
        # cross-delivery rule: a tmpfs mask beats even a cred copy-sync).
        rec = reconcile_categories([
            _entry(c, box_dest=D)
            for c in ("synced", "common", "caches", "seeded", "masks")
        ])
        assert [w.category for w in rec.mounts + rec.copies] == ["masks"]
        # synced still beats every non-mask mount (cross-delivery, unchanged);
        # the common/caches pair underneath it is a same-scope row-5 ambiguity.
        rec = reconcile_categories([
            _entry(c, box_dest=D) for c in ("common", "caches", "seeded", "synced")
        ])
        assert [w.category for w in rec.mounts + rec.copies] == ["synced"]
        assert len(rec.warnings) == 1
        # A mount beats a seeded copy (cross-delivery, unchanged).
        rec = reconcile_categories([
            _entry(c, box_dest=D) for c in ("seeded", "caches")
        ])
        assert [w.category for w in rec.mounts + rec.copies] == ["caches"]

    def test_mask_overrides_every_other_category_at_one_dest(self):
        """Spec §0 row 2 — the ONE rung of the retired ladder that survives.

        A mask says NOTHING MAY BE HERE, so it overrides whatever else lands at
        the dest. The binding is deliberately absent: mixing the concrete layer
        with an abstraction is row 3 (an error), which is asserted separately.
        """
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.seeded.s": ["/h/s", "/g/x"],
                    "box.caches.c": ["/h/c", "/g/x"],
                    "box.common.h": ["/h/h", "/g/x"],
                    "box.masks": "/g/x",
                },
            ),
        ]
        rec = _reconcile(levels, ctx)
        winners = rec.mounts + rec.copies
        assert [w.category for w in winners] == ["masks"]

    def test_mask_does_not_excuse_a_contradictory_pair_underneath_it(self):
        """Rows 1/3 are evaluated BEFORE the row-2 override.

        §0 states row 1 as "ERROR, always — any scope, any mode". A mask that
        happens to cover a contradiction hides its consequence, not the
        contradiction, so the error still fires. Pinned because the opposite
        ordering is an equally implementable reading.
        """
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([
                _entry("bindings.rw", box_dest="/g/x", name="b"),
                _entry("common", box_dest="/g/x", name="h"),
                _entry("masks", box_dest="/g/x"),
            ])
        assert exc.value.kind == "extension_onto_occupied"

    def test_seed_beats_nothing_alone_survives(self):
        # A lone seed at a dest with no higher-authority collider survives.
        rec = reconcile_categories([_entry("seeded", box_dest="/g/only")])
        assert [w.category for w in rec.copies] == ["seeded"]
        assert rec.mounts == []


class TestReconcileScopePrecedence:
    def test_two_bindings_at_one_dest_no_longer_tie_break_they_ERROR(self):
        """INVERTED by spec §0 row 1 (was: the more specific scope won silently).

        This is the M-7 upgrade hazard in one assertion: the exact configuration
        that used to resolve to ``/box`` now refuses to launch.
        """
        sys_e = _entry("bindings.rw", box_dest="/g/d", scope="system", host_src="/sys")
        box_e = _entry("bindings.rw", box_dest="/g/d", scope="box", host_src="/box")
        with pytest.raises(CategoryCollisionError) as exc:
            reconcile_categories([sys_e, box_e])
        assert exc.value.kind == "binding_vs_binding"
        assert exc.value.box_dest == "/g/d"

    def test_tie_within_category_box_wins_regardless_of_input_order(self):
        sys_e = _entry("caches", box_dest="/g/c", scope="system", host_src="/sys")
        box_e = _entry("caches", box_dest="/g/c", scope="box", host_src="/box")
        # box listed FIRST: still wins (scope beats input order).
        rec = reconcile_categories([box_e, sys_e])
        assert rec.mounts[0].host_src == "/box"


class TestReconcileSyncedBindingError:
    def test_synced_vs_binding_same_dest_raises(self):
        synced = _entry("synced", box_dest="/g/clash")
        binding = _entry("bindings.rw", box_dest="/g/clash")
        with pytest.raises(ConfigError) as exc:
            reconcile_categories([synced, binding])
        assert "/g/clash" in str(exc.value)

    def test_synced_vs_binding_ro_same_dest_raises(self):
        synced = _entry("synced", box_dest="/g/clash")
        binding = _entry("bindings.ro", box_dest="/g/clash")
        with pytest.raises(ConfigError):
            reconcile_categories([synced, binding])

    def test_synced_and_shared_same_dest_is_not_an_error(self):
        # Only synced<->binding is the hard error; synced beats shared cleanly.
        synced = _entry("synced", box_dest="/g/ok")
        shared = _entry("common", box_dest="/g/ok")
        rec = reconcile_categories([synced, shared])
        assert [w.category for w in (rec.mounts + rec.copies)] == ["synced"]


class TestReconcileDepthOrder:
    def test_mounts_emitted_shallow_to_deep(self):
        home = _entry("bindings.rw", box_dest="/home/agent")
        ws = _entry("bindings.rw", box_dest="/home/agent/workspace")
        vault = _entry("masks", box_dest="/home/agent/workspace/vault")
        # Input deliberately scrambled.
        rec = reconcile_categories([vault, home, ws])
        assert [m.box_dest for m in rec.mounts] == [
            "/home/agent",
            "/home/agent/workspace",
            "/home/agent/workspace/vault",
        ]

    def test_mask_inside_workspace_lands_on_top(self):
        # mask at deeper dest emits AFTER the workspace binding (last -v wins).
        ws = _entry("bindings.rw", box_dest="/home/agent/workspace")
        mask = _entry("masks", box_dest="/home/agent/workspace/vault")
        rec = reconcile_categories([mask, ws])
        assert [m.box_dest for m in rec.mounts] == [
            "/home/agent/workspace",
            "/home/agent/workspace/vault",
        ]
        assert rec.mounts[-1].category == "masks"

    def test_root_home_before_nested(self):
        root = _entry("bindings.rw", box_dest="/")
        nested = _entry("bindings.rw", box_dest="/home/agent/workspace/vault")
        rec = reconcile_categories([nested, root])
        assert [m.box_dest for m in rec.mounts] == [
            "/",
            "/home/agent/workspace/vault",
        ]


class TestReconcileGroupAuthGate:
    def test_shares_false_suppresses_synced(self):
        synced = _entry("synced", box_dest="/g/cred")
        binding = _entry("bindings.rw", box_dest="/g/keep")
        rec = reconcile_categories([synced, binding], deliver_creds=False)
        cats = [w.category for w in (rec.mounts + rec.copies)]
        assert "synced" not in cats
        assert "bindings.rw" in cats

    def test_shares_false_suppresses_credential_seed_only(self):
        cred_seed = _entry("seeded", box_dest="/g/cred", is_credential=True)
        plain_seed = _entry("seeded", box_dest="/g/plain", is_credential=False)
        rec = reconcile_categories([cred_seed, plain_seed], deliver_creds=False)
        dests = [c.box_dest for c in rec.copies]
        assert "/g/cred" not in dests
        assert "/g/plain" in dests

    def test_shares_true_keeps_synced_and_cred_seed(self):
        synced = _entry("synced", box_dest="/g/s")
        cred_seed = _entry("seeded", box_dest="/g/cred", is_credential=True)
        rec = reconcile_categories([synced, cred_seed], deliver_creds=True)
        dests = {c.box_dest for c in rec.copies}
        assert dests == {"/g/s", "/g/cred"}

    def test_gate_applied_before_collision_so_no_false_synced_binding_error(self):
        # synced suppressed by gate -> no clash with the binding at same dest.
        synced = _entry("synced", box_dest="/g/d")
        binding = _entry("bindings.rw", box_dest="/g/d")
        rec = reconcile_categories([synced, binding], deliver_creds=False)
        assert [m.category for m in rec.mounts] == ["bindings.rw"]


class TestReconcilePartition:
    def test_copy_mount_env_split(self):
        ctx = make_ctx()
        levels = [
            LevelView(
                "box",
                {
                    "box.seeded.s": ["/h/s", "/g/s"],
                    "box.synced.y": ["/h/y", "/g/y"],
                    "box.bindings.rw.b": ["/h/b", "/g/b"],
                    "box.masks": "/g/m",
                    "box.env.FOO": "bar",
                },
            ),
        ]
        rec = _reconcile(levels, ctx)
        assert {m.delivery for m in rec.mounts} == {MOUNT}
        assert {c.delivery for c in rec.copies} == {COPY}
        assert [e.box_dest for e in rec.envs] == ["FOO"]
        assert {e.delivery for e in rec.envs} == {ENV}

    def test_env_never_collides_with_path_dest(self):
        # An env VAR name equal to a path dest must not be treated as a collision.
        env = _entry("env", box_dest="/g/x")  # contrived VAR name
        binding = _entry("bindings.rw", box_dest="/g/x")
        rec = reconcile_categories([env, binding])
        assert len(rec.mounts) == 1
        assert len(rec.envs) == 1


# ---------------------------------------------------------------------------
# core_default_categories (step 3) — the core box mounts (home/workspace/vault)
# routed through the resolver as AGENT-level default_categories.
# ---------------------------------------------------------------------------


class _FakeProj:
    """Minimal ProjectPaths stand-in for core_default_categories."""

    def __init__(self, tmp_path, *, vault_dirs: bool):
        from pathlib import Path

        self.shell_path = Path("/host/shell")
        self.project_path = Path("/host/proj")
        self.vault_ro_path = tmp_path / "vro"
        self.vault_rw_path = tmp_path / "vrw"
        if vault_dirs:
            self.vault_ro_path.mkdir()
            self.vault_rw_path.mkdir()


class TestCoreDefaultCategories:
    """core_defaults.core_default_categories emits the structured core binds."""

    def test_home_and_workspace_structured_triples(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="primary",
        )
        # home: rw bind, dest ~ , options Z,U (the structured 3-tuple's 3rd slot).
        # The home host_src is the MODE-INDEPENDENT @meta.box.path/home REF (spec §2c
        # ALL PROJECTS), resolved at launch-expand to str(proj.shell_path).  The
        # per-mode variation lives in meta.box.path, not here.
        assert binds["box.bindings.rw.home"] == (
            "@meta.box.path/home",
            "~",
            "Z,U",
        )
        # workspace: rw bind, dest ~/workspace, options Z,U.  B2: the host_src is the
        # @meta.box.workspace REF (routed through the materialized identity anchor,
        # spec §2c L476); it resolves to str(proj.project_path) at launch-expand.
        assert binds["box.bindings.rw.workspace"] == (
            "@meta.box.workspace",
            "~/workspace",
            "Z,U",
        )
        # Every value is a structured 3-tuple (spec §2a), never a colon-string.
        for v in binds.values():
            assert isinstance(v, tuple) and len(v) == 3
            assert ":" not in v[1]

    def test_home_is_mode_independent_and_vault_roots_at_workset(self, tmp_path):
        """The home DECLARATION is one line for every mode; vault roots at @workset.*.

        Spec §2c ALL PROJECTS gives ``box.bindings.rw.home`` a single mode-independent
        form, so STANDALONE emits the SAME tuple as primary/named — the per-mode
        variation moved up into ``meta.box.path``.  The vault bind stays per-mode (a
        lone box has no per-box ``/@meta.box.name`` subdir) but BOTH arms now root at
        the SAME ``@workset.vault_*`` anchor; the standalone arm used to carry a
        SECOND spelling of that root (``@meta.workset.path/vault/*``).
        """
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="standalone",
        )
        primary = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="primary",
        )
        assert binds["box.bindings.rw.home"] == ("@meta.box.path/home", "~", "Z,U")
        # ONE declaration: byte-equal to the primary arm.
        assert binds["box.bindings.rw.home"] == primary["box.bindings.rw.home"]
        assert binds["box.bindings.ro.vault"] == (
            "@workset.vault_ro", "~/vault/ro", "ro",
        )
        assert binds["box.bindings.rw.vault"] == (
            "@workset.vault_rw", "~/vault/rw", "Z,U",
        )
        # The vault BIND is still per-mode — primary/named carry the box-name leaf.
        assert primary["box.bindings.ro.vault"] == (
            "@workset.vault_ro/@meta.box.name", "~/vault/ro", "ro",
        )

    def test_vault_keys_present_when_enabled_and_dirs_exist(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="primary",
        )
        # B2b: PRIMARY vault host_src routes through @workset.vault_{ro,rw}/
        # @meta.box.name (spec §2c L442/445), resolved at launch to the proj vault.
        assert binds["box.bindings.ro.vault"] == (
            "@workset.vault_ro/@meta.box.name",
            "~/vault/ro",
            "ro",
        )
        assert binds["box.bindings.rw.vault"] == (
            "@workset.vault_rw/@meta.box.name",
            "~/vault/rw",
            "Z,U",
        )

    def test_vault_keys_absent_when_disabled(self, tmp_path):
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        binds = core_defaults.core_default_categories(
            None, proj, enable_vault=False, mode="primary",
        )
        assert "box.bindings.ro.vault" not in binds
        assert "box.bindings.rw.vault" not in binds
        # home + workspace stay unconditional.
        assert "box.bindings.rw.home" in binds
        assert "box.bindings.rw.workspace" in binds

    def test_vault_emitted_and_source_created_when_missing(self, tmp_path):
        """Vault is UNIVERSAL unless disabled: a missing source is CREATED and the
        bind is still emitted (create-if-missing), not silently dropped.

        The create-if-missing gate keys off the PROBED proj vault source (unchanged
        by B2b); only the emitted host_src is now the @-ref (resolved at launch)."""
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=False)  # vault dirs do NOT exist yet
        assert not proj.vault_ro_path.exists()
        assert not proj.vault_rw_path.exists()

        binds = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="primary",
        )

        # The bind is emitted even though the source did not exist (host_src = @-ref).
        assert binds["box.bindings.ro.vault"] == (
            "@workset.vault_ro/@meta.box.name",
            "~/vault/ro",
            "ro",
        )
        assert binds["box.bindings.rw.vault"] == (
            "@workset.vault_rw/@meta.box.name",
            "~/vault/rw",
            "Z,U",
        )
        # ...and the missing source dirs were created (create-if-missing) — the gate
        # still keys off the PROBED proj vault source, independent of the @-ref.
        assert proj.vault_ro_path.is_dir()
        assert proj.vault_rw_path.is_dir()
        assert "box.bindings.rw.home" in binds
        assert "box.bindings.rw.workspace" in binds

    def test_options_flow_through_resolver_to_entry(self, tmp_path):
        """The 3rd-slot options survive flawed_oracle_categories as the entry options.

        Injected as AGENT-level defaults, the structured triples resolve to
        CategoryEntry with the per-entry options override applied (so vault_ro keeps
        ``ro`` and the rw binds keep ``Z,U``) — proving the options slot flows
        end-to-end to the emitted Mount.
        """
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        defaults = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="primary",
        )
        ctx = make_ctx()
        # workspace routes through @meta.box.workspace; home through the RO box
        # root @meta.box.path; vault through @workset.vault_*/@meta.box.name
        # (PRIMARY).  Provide the materialized anchors (as the launch floor does) so
        # the refs resolve.  The box root carries its @-REF FORMULA, not a resolved
        # literal, so this stays sensitive to a wrong formula: the oracle resolves
        # @-refs TRANSITIVELY, exactly as the launch expand does.  The keys must be
        # LISTED, though — this dict IS the whole keyspace for the oracle, so an
        # omitted key is simply an unknown @-reference.
        levels = [
            LevelView("box", {
                "meta.box.workspace": str(proj.project_path),
                "meta.box.name": "mybox",
                "meta.box.path": "@workset.boxes/@meta.box.name",
                "workset.boxes": "/data/pw/boxes",
                "workset.vault_ro": "/data/pw/vault/ro",
                "workset.vault_rw": "/data/pw/vault/rw",
            }),
            LevelView("agent", {}, defaults=defaults),
        ]
        entries = _resolve(levels, ctx)
        by_dest = {e.box_dest: e for e in entries}
        assert by_dest["/home/agent"].options == "Z,U"
        assert by_dest["/home/agent"].category == "bindings.rw"
        assert by_dest["/home/agent/workspace"].options == "Z,U"
        assert by_dest["/home/agent/vault/ro"].options == "ro"
        assert by_dest["/home/agent/vault/ro"].category == "bindings.ro"
        assert by_dest["/home/agent/vault/rw"].options == "Z,U"

    def test_home_and_workspace_depth_order_keeps_both(self, tmp_path):
        """reconcile depth-sort keeps BOTH the nested home + workspace binds."""
        from kanibako import core_defaults

        proj = _FakeProj(tmp_path, vault_dirs=True)
        defaults = core_defaults.core_default_categories(
            None, proj, enable_vault=True, mode="primary",
        )
        ctx = make_ctx()
        # workspace via @meta.box.workspace; home via the RO box root
        # @meta.box.path; vault via @workset.vault_*/@meta.box.name (PRIMARY).
        # The box root carries its @-REF FORMULA (the oracle resolves @-refs
        # transitively), so a wrong formula would show up here; every key it walks
        # must be LISTED, since this dict IS the oracle's whole keyspace.
        levels = [
            LevelView("box", {
                "meta.box.workspace": str(proj.project_path),
                "meta.box.name": "mybox",
                "meta.box.path": "@workset.boxes/@meta.box.name",
                "workset.boxes": "/data/pw/boxes",
                "workset.vault_ro": "/data/pw/vault/ro",
                "workset.vault_rw": "/data/pw/vault/rw",
            }),
            LevelView("agent", {}, defaults=defaults),
        ]
        rec = _reconcile(levels, ctx)
        dests = [m.box_dest for m in rec.mounts]
        # Both kept; home (shallower) emitted before workspace (deeper).
        assert "/home/agent" in dests
        assert "/home/agent/workspace" in dests
        assert dests.index("/home/agent") < dests.index("/home/agent/workspace")
