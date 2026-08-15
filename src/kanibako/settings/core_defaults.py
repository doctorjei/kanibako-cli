"""System/core category defaults — thin reader of the shipped ``core-defaults.yaml``.

**_Terminology_**
- _STATIC_: box-side dests, options, structural shape — read straight from the file
- _DYNAMIC_: host SOURCES, runtime-probed and injected at the seam (the file names them
  symbolically)
- _CONDITIONAL_: per-mode / per-state gates, applied at the injection site, never in the file

Eight declared BIND families, one producer each, plus two NON-BIND scalar sections
(``agent_default:`` behavior, ``env:`` static variables); every table lands in the BASE-level
floor. The box-create canon SKELETON also lives here — mirror image of the canon binds
(llm-docs).
"""

from __future__ import annotations

import importlib.resources
import logging
from collections.abc import Iterable
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from kanibako.settings.paths import ProjectPaths, StandardPaths
    from kanibako.targets.base import PluginDescriptor, Target


def packaged_data_dir(*parts: str) -> Traversable:
    """Resolve a path inside the packaged ``kanibako.data`` tree; the ONE ``files()`` join."""
    return importlib.resources.files("kanibako.data").joinpath(*parts)

# Filename of the shipped system/core defaults (in kanibako.data).
CORE_DEFAULTS_FILENAME = "core-defaults.yaml"

# ⚑ ``FLOOR_PLACEHOLDER_SRC`` / ``core_default_bind_keys`` (the SET-TIME floor registry) were
# deleted with R-9; do not reintroduce one — the producers below are a different, LIVE mechanism.


def _load_doc() -> dict[str, Any]:
    """Read and parse the bundled system/core defaults file."""
    ref = packaged_data_dir(CORE_DEFAULTS_FILENAME)
    raw = yaml.safe_load(Path(str(ref)).read_text()) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def vault_mask_default() -> list[str]:
    """Return the default masked DESTINATIONS — now EMPTY (no default mask)."""
    masks = _load_doc().get("masks", {})
    return [str(m) for m in masks]


def behavior_defaults() -> dict[str, str]:
    """Return the declared ``agent.default.<key>`` BEHAVIOR floor (spec §2d).

    The all-agents backstop, merged UNDER a plugin's descriptor floor at the launch
    sites (descriptor last ⇒ a plugin's declared default still wins).  ⚑ Values are
    STRINGS: the consumers run them through ``coerce_bool`` and
    ``effective_behavior`` stringifies, so a YAML bool would arrive as ``"True"``.
    """
    return {
        str(key): str(value)
        for key, value in (_load_doc().get("agent_default") or {}).items()
    }


def behavior_default(key: str) -> str:
    """ONE declared ``agent.default.<key>`` value — the FAIL-CLOSED single-key read.

    ⚑ THE ONE SPELLING of this read.  ``start._declared_behavior`` and
    ``settings_keyspace.access_default`` both come here; a second fail-closed copy is
    how the two would drift.
    ⚑ A FUNCTION, not a constant: :func:`_load_doc` re-reads the shipped file on every
    call, so a module-level read would bind the value at IMPORT time.
    ⚑ An absent declaration RAISES — it is a PACKAGING defect, and re-materializing a
    literal here would be exactly the consumer-side default this read replaced.
    """
    defaults = behavior_defaults()
    if key not in defaults:
        raise RuntimeError(
            f"{CORE_DEFAULTS_FILENAME} declares no 'agent_default.{key}' — the core "
            f"behavior floor (spec §2d agent.default.{key}) lives there and nowhere else."
        )
    return defaults[key]


def env_default_categories() -> dict[str, str]:
    """The STATIC core env floor as ``<scope>.env.<VAR>`` keys (spec §2d).

    ⚑ A SEPARATE emitter from ``start._core_env_default_categories``, whose docstring
    forbids new entries: that one carries the launch-DERIVED ``KANIBAKO_*`` stamps —
    values that do not exist until a launch runs — while these are literals a file can
    hold.  The derived table merges AFTER this one, so a stamp still wins a VAR this
    section also names.
    ⚑ Values are STRINGS, as in :func:`behavior_defaults`: an unquoted YAML bool would
    reach the box as ``"True"``.

    ⚑ FAIL-CLOSED ON THE KEY IT BUILDS (R3).  Every emitted ``<scope>.env.<VAR>`` is
    matched against ``settings_categories.ENV_KEY_RE`` — the keyspace's own
    declaration of the family (spec §2a) — so a typo'd scope head (``sytem:``) or a
    VAR that is not an env-name RAISES, naming the file and the head.  It used to be
    a silent no-op: the bad key entered ``default_categories``, matched nothing
    downstream and simply never reached a box.
    """
    table: dict[str, str] = {}
    section = _load_doc().get("env") or {}
    if not isinstance(section, dict):
        raise RuntimeError(
            f"{CORE_DEFAULTS_FILENAME} 'env:' must be scope-keyed tables, got "
            f"{type(section).__name__} — see the section's own comment for the shape."
        )
    for scope, entries in section.items():
        if entries is None:
            continue
        if not isinstance(entries, dict):
            raise RuntimeError(
                f"{CORE_DEFAULTS_FILENAME} declares 'env.{scope}' as "
                f"{type(entries).__name__}, not a table of <VAR>: \"<value>\" — a "
                f"variable belongs under the scope that owns it."
            )
        for var, value in entries.items():
            _check_env_key(str(scope), str(var))
            table[f"{scope}.env.{var}"] = str(value)
    return table


def _check_env_key(scope: str, var: str) -> None:
    """RAISE unless ``<scope>.env.<VAR>`` is a declared key (spec §2a); name which half is wrong.

    ⚑ ONE RULE, ASKED TWICE.  ``ENV_KEY_RE`` carries both halves — the legal scope
    heads and the env-name shape — so the second match is a PROBE that tells the two
    apart for the message, not a second copy of either.  Re-spelling the head set
    here is exactly the drift the regex import avoids.
    ⚑ A FUNCTION-LOCAL import, the :func:`add_bind` pattern: this module stays a thin
    reader whose module scope pulls in no part of the settings stack, and
    ``settings_categories`` imports nothing from ``kanibako`` at module scope, so
    there is no cycle either way.
    """
    from kanibako.settings.settings_categories import ENV_KEY_RE

    if ENV_KEY_RE.match(f"{scope}.env.{var}") is not None:
        return
    head_ok = ENV_KEY_RE.match(f"{scope}.env.PROBE") is not None
    detail = (
        f"'{var}' is not an env-var name ([A-Za-z_][A-Za-z0-9_]*)" if head_ok
        else f"'{scope}' is not a scope the env family is declared at — spec §2a "
             f"allows system, workset, box and agent.<node> (bare 'agent' is not a key)"
    )
    raise RuntimeError(
        f"{CORE_DEFAULTS_FILENAME} declares 'env.{scope}.{var}', which is not a "
        f"key: {detail}."
    )


#: A dest-keyed floor bind table: ``{"box.bindings.ro": {box_dest: (src[, opts])}}`` (R-5/R-11).
#: ⚑ RAW — parsed by ``settings_assemble.dotted_partial``, never here.
BindArmTable = dict[str, dict[str, tuple[str, ...]]]


def add_bind(
    binds: dict[str, Any],
    category: str,
    box_dest: str,
    host_src: str,
    options: str | None = None,
    *,
    scope: str = "box",
) -> None:
    """Install ONE dest-keyed entry into the ``<scope>.<category>`` arm of *binds* (R-3/R-6/R-11).

    ⚑ ``seeded`` / ``synced`` are COPIES and stay copies — what is DONE with an entry is
    ``settings_categories._DELIVERY``'s answer, not this function's.
    ⚑ The DEST is normalized; *host_src* is stored exactly as given.
    """
    from kanibako.settings.settings_resolve import normalize_bind_dest

    arm_key = f"{scope}.{category}"
    arm = binds.setdefault(arm_key, {})
    dest = normalize_bind_dest(str(box_dest))
    if dest in arm:
        raise ValueError(
            f"{arm_key} declares two floor entries at one destination "
            f"{dest!r} ({arm[dest][0]!r} and {host_src!r}); a dest-keyed "
            f"category admits one entry per destination."
        )
    arm[dest] = (host_src,) if options is None else (host_src, str(options))


def channel_default_categories(
    std: StandardPaths, proj: ProjectPaths
) -> BindArmTable:
    """Build the per-mode channel bind table as ``default_categories`` (§2c/§2f)."""
    from kanibako.channels import channels as _ch

    addr = _ch.box_channel_addresses(proj, std)
    wch = _ch.workset_channel_paths(proj, std)

    # Symbolic source name -> runtime-probed host path.  ⚑ Workset sources exist only for
    # PRIMARY/NAMED, and their absence IS the standalone-omit gate below.
    sources: dict[str, str] = {
        "channels_common": str(std.channels_common),
        "channels_chat": str(std.channels_chat),
        "channels_share": str(std.channels_share),
        "channels_mailboxes": str(std.channels_mailboxes),
        "inbox": str(addr.inbox),
    }
    if wch is not None:
        sources["workset_common"] = str(wch.common)
        sources["workset_chat"] = str(wch.chat)
        sources["workset_share"] = str(wch.share)

    binds: BindArmTable = {}
    for entry in _load_doc().get("channels", []):
        source = entry["source"]
        if source not in sources:
            # Workset-scoped entry on a standalone box: no host source → omit.
            continue
        # ⚑ B2: a ``meta_ref`` entry emits the @-ref STRING as host_src (spec §2c); the
        # ``source`` gate above still applies to it.
        host_src = entry.get("meta_ref", sources[source])
        add_bind(binds, "bindings.rw", str(entry["box_dest"]), host_src)
    return binds


def core_default_categories(
    std: StandardPaths, proj: ProjectPaths, *, enable_vault: bool, mode: str,
    guarantee_create: bool = True,
) -> BindArmTable:
    """Build the core box mounts — workspace + vault — as ``default_categories`` (step 3).

    ⚑ NO HOME ROW. Home is pid 0, not one bind among these: it is constructed at the
    assembly seam off ``meta.box.home`` (spec ``:1015``), never declared here.
    """
    # Symbolic source name -> runtime-probed host path off ``ProjectPaths``.
    sources: dict[str, str] = {
        "project_path": str(proj.project_path),
        "vault_ro_path": str(proj.vault_ro_path),
        "vault_rw_path": str(proj.vault_rw_path),
    }
    vault_dir = {
        "vault_ro_path": proj.vault_ro_path,
        "vault_rw_path": proj.vault_rw_path,
    }

    binds: BindArmTable = {}
    for entry in _load_doc().get("core", []):
        # Vault binds are UNIVERSAL unless explicitly disabled; only a disabled vault skips.
        if entry.get("scope") == "vault":
            if not enable_vault:
                continue
            src_path = vault_dir.get(entry["source"])
            if src_path is None:
                continue  # unknown source name (defensive)
            # ⚑ *guarantee_create* False suppresses ONLY this mkdir — the bind is still emitted;
            # a DISPLAY verb (``box show --effective``) must not write to disk.
            if guarantee_create:
                src_path.mkdir(parents=True, exist_ok=True)
        category = entry["category"]
        # ⚑ TWO @-ref shapes: a single ``meta_ref`` (mode-independent — workspace) or a
        # ``mode_meta_ref`` PER-MODE map, which today serves the VAULT binds ONLY (spec §2c).
        mode_ref = entry.get("mode_meta_ref")
        if mode_ref is not None:
            host_src = mode_ref[mode]
        else:
            host_src = entry.get("meta_ref", sources[entry["source"]])
        add_bind(
            binds, category, str(entry["box_dest"]), host_src,
            str(entry["options"]),
        )
    return binds


def kani_default_categories() -> BindArmTable:
    """Build the kanibako CLI binds — the UNCONDITIONAL trio — as ``default_categories``."""
    import importlib.resources

    import kanibako

    pkg_dir = Path(kanibako.__file__).parent
    entry_ref = importlib.resources.files("kanibako.scripts").joinpath(
        "kanibako-entry"
    )
    entry_path = Path(str(entry_ref))
    secrets_ref = importlib.resources.files("kanibako.scripts").joinpath(
        "kanibako-secrets.sh"
    )
    secrets_path = Path(str(secrets_ref))

    sources: dict[str, str] = {
        "kani_pkg": str(pkg_dir),
        "kani_bin": str(entry_path),
        "secret_export": str(secrets_path),
    }

    binds: BindArmTable = {}
    for entry in _load_doc().get("kani", []):
        category = entry["category"]
        add_bind(
            binds, category, str(entry["box_dest"]), sources[entry["source"]],
            str(entry["options"]),
        )
    return binds


# ===========================================================================
# The KICKOFF LOADER — the directive-chain ENTRY SLOT (spec §2c, P-5).
# ===========================================================================

# The packaged kickoff loader, relative to the ``kanibako.data`` root; FLAT under ``global/``
# beside ``global/rom`` (RO canon) and ``global/template`` (writable seed), being neither.
KICKOFF_PACKAGED_PARTS = ("global", "KICKOFF.md")


def _kickoff_entry() -> dict[str, Any]:
    """The one declarative ``kickoff:`` entry, or RAISE if the shipped file lost it."""
    entries = _load_doc().get("kickoff") or []
    if not isinstance(entries, list) or len(entries) != 1:
        raise RuntimeError(
            f"{CORE_DEFAULTS_FILENAME} must declare EXACTLY ONE 'kickoff:' entry "
            f"(got {entries!r}) — the directive-chain entry slot (spec §2c "
            "box.bindings.ro[~/.config/kanibako/kickoff.md]) is declared there and "
            "read back by both the "
            "bind emitter and the KANIBAKO_DIRECTIVE_SEED env var."
        )
    return entries[0]


def kickoff_box_dest() -> str:
    """The ``~``-spelled box-side kickoff slot — SINGLE SOURCE OF TRUTH, read from the file."""
    return str(_kickoff_entry()["box_dest"])


def kickoff_guest_dest() -> str:
    """:func:`kickoff_box_dest` as an ABSOLUTE guest path (the env var + the transition gate)."""
    from kanibako.settings.settings_resolve import GUEST_HOME

    dest = kickoff_box_dest()
    return GUEST_HOME + dest[1:] if dest.startswith("~") else dest


def kickoff_default_categories(
    descriptor: "PluginDescriptor | None" = None,
) -> BindArmTable:
    """Build the core KICKOFF bind as ``default_categories`` (spec §2c, P-5)."""
    from kanibako.targets.assembly import declares_box_dest

    # ⚑⚑ THE TRANSITION GATE — core YIELDS to a plugin that still ships a kickoff (M-12).
    # ⚑ REMOVAL CONDITION in llm-docs; re-verify against the three plugins before deleting.
    if declares_box_dest(descriptor, kickoff_guest_dest()):
        return {}

    entry = _kickoff_entry()
    # The host SOURCE, resolved from the entry's SYMBOLIC name (kani parity).
    sources = {"kickoff": Path(str(packaged_data_dir(*KICKOFF_PACKAGED_PARTS)))}
    src = sources[str(entry["source"])]
    # ⚑ FAIL-CLOSED: a missing loader means NO directive chain at all, silently.
    if not src.is_file():
        raise RuntimeError(
            f"the packaged kickoff loader is missing at {src} — it is the entry "
            "point of the whole directive chain (spec §2c "
            "box.bindings.ro[~/.config/kanibako/kickoff.md]), "
            "so a box launched without it would run with NO directives at all. This "
            "is a PACKAGING defect; refusing to launch."
        )
    binds: BindArmTable = {}
    add_bind(
        binds, str(entry["category"]), str(entry["box_dest"]), str(src),
        str(entry["options"]),
    )
    return binds


# The packaged rom root — the READ-ONLY built-in CANON content; RO-bind DUAL of the
# ``("global","template")`` writable seed in :func:`templates._packaged_base_template`.
ROM_ROOT_PARTS = ("global", "rom")

# ⚑ The packaged rom tree is FLAT (J-7) and does NOT mirror the guest layout: a rom-relative
# path is NOT its own ``~/``-dest — every guest dest goes through :func:`_canon_dest`.
CANON_GUEST_ROOT = "canon"

# The rom-ROOT-relative posix paths of the packaged CANON bind SOURCES (spec §2c).
ROM_COLLECTION_REL = "COLLECTION.md"
ROM_BIBLE_REL = "bible"
ROM_CONTENTS_REL = f"{ROM_BIBLE_REL}/ROM_CONTENTS.md"

# The handbook BOOK root, guest-only (nothing packages a handbook); beside ``ROM_BIBLE_REL``
# because the managed-region deny list below needs both book roots.
HANDBOOK_REL = "handbook"

# ⚑ The load-bearing box guide (the bible's GENERAL chapter), rom-root-relative; it MUST ship
# whenever the rom root is populated — see the fail-closed guard in ``rom_default_categories``.
ROM_GUIDE_REL = "bible/general/directives/ROM_GENERAL.md"

# The bible chapters core PACKAGES, one whole-directory sibling bind each.  ⚑ Deliberately no
# ``agent``: J-7 retired the packaged placeholder chapter with the nested-bind model.
ROM_BIBLE_CHAPTERS = ("general", "workset", "box")

# The bible's PLUGIN chapter.  Guest-only: a mountpoint the box-create skeleton materialises.
BIBLE_AGENT_CHAPTER = "agent"

# The plugin-rom EMISSION GATE marker, relative to a plugin's ``data/rom`` chapter root.
PLUGIN_CHAPTER_MARKER_REL = "directives/ROM_AGENT.md"

# The MANAGED CANON REGION no template seed may write into (spec §2c), as ``~``-relative
# PREFIXES.  ⚑ Prefixes, not the literal bind dests: under J-7 ``canon/bible`` is not itself a
# dest, so literal dests would stop rejecting a seed at ``canon/bible/agent/x.md``.
# ⚑ ``canon/handbook`` is here on the SKELETON's authority ("does box create own it?"), which
# holds independently of what is bound.
CANON_SEED_DENY_PREFIXES = (
    f"{CANON_GUEST_ROOT}/COLLECTION.md",
    f"{CANON_GUEST_ROOT}/{ROM_BIBLE_REL}",
    f"{CANON_GUEST_ROOT}/{HANDBOOK_REL}",
)


def _canon_dest(rel: str) -> str:
    """Return the ``~``-relative guest dest for a BOOK-ROOT-relative canon path *rel*."""
    return f"~/{CANON_GUEST_ROOT}/{rel}"


def assert_canon_bind_seed_disjoint(
    bind_dests: Iterable[str], seed_rels: Iterable[str],
) -> None:
    """RAISE if any template SEED lands at or under a MANAGED ``~/canon`` path (spec §2c).

    ⚑⚑ BOTH ARGUMENTS MUST BE ``~``-RELATIVE or every comparison silently misses.
    ⚑ WIDENING THE INPUTS IS THE CALLER'S JOB — hence parameters, not computed inside.
    PREFIX CONTAINMENT, not set intersection.
    """
    dests = sorted(set(bind_dests))
    violations: list[str] = []
    for rel in sorted(set(seed_rels)):
        for dest in dests:
            if rel == dest or rel.startswith(f"{dest}/"):
                violations.append(
                    f"{rel!r} is at/under the managed canon path {dest!r}"
                )
    if violations:
        raise RuntimeError(
            "template seed lands in the MANAGED canon region (box create "
            "materialises that region ROOT-OWNED, so the copy FAILS WITH EACCES "
            "AT CREATE — it does not silently lose, it stops the create with an "
            "OS error; and even where a copy could land, the mount SHADOWS it at "
            "the same path regardless of order, so the content would be invisible "
            "— not merged, not an error):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\nSeeds target canon/{notebook,workbook} ONLY (spec §2c)."
        )


def rom_default_categories() -> BindArmTable:
    """Build the FIVE read-only packaged-CANON binds as ``default_categories`` (J-7, spec §2c).

    ⚑⚑ SIBLINGS, NOT A WHOLE-DIR BOOK: every entry lands on a mountpoint
    :func:`materialize_canon_skeleton` already made, so no mountpoint lives inside a bind SOURCE.
    ⚑ ``bible/agent/`` is deliberately NOT required and must NOT ship.
    """
    from kanibako.launch import templates

    rom_root = Path(str(packaged_data_dir(*ROM_ROOT_PARTS)))
    if not rom_root.is_dir():
        return {}

    rom_files = templates.walk_shipped_files(rom_root)
    rom_rels = {rel for rel, _ in rom_files}

    # ⚑ FAIL-CLOSED (a): anchored to the guide's ON-DISK presence, NOT to a non-empty filtered
    # list — that is what catches the over-broad-filter / empty-glob / broken-walk class.
    guide_shipped = (rom_root / ROM_GUIDE_REL).is_file()
    if guide_shipped and ROM_GUIDE_REL not in rom_rels:
        raise RuntimeError(
            "rom shipped-file walk is missing the load-bearing box guide "
            f"{ROM_GUIDE_REL!r} (the guide file ships under rom root {rom_root} but "
            f"the walk produced {sorted(rom_rels)}); refusing to launch a box "
            "without the guide."
        )

    # A genuinely empty rom root is a no-rom install — emit nothing.  Reached only when the
    # guide is NOT on disk (guard (a) above already raised if it was).
    if not rom_files:
        return {}

    # The SIBLING bind set, as ``(key-leaf, rom-relative source, is_dir)``.  ⚑ ONE declaration
    # drives BOTH the completeness guard and the emission, so they cannot drift apart.
    binds: list[tuple[str, str, bool]] = [
        ("canon_collection", ROM_COLLECTION_REL, False),
        ("canon_bible_contents", ROM_CONTENTS_REL, False),
        *(
            (f"canon_bible_{chapter}", f"{ROM_BIBLE_REL}/{chapter}", True)
            for chapter in ROM_BIBLE_CHAPTERS
        ),
    ]

    # ⚑ FAIL-CLOSED (b): a POPULATED rom root must carry the WHOLE payload — every emitted
    # bind's SOURCE, plus the guide (which has no bind of its own: it rides ``general``'s).
    present: list[tuple[str, bool]] = [
        (rel, (rom_root / rel).is_dir() if is_dir else (rom_root / rel).is_file())
        for _key, rel, is_dir in binds
    ]
    present.append((ROM_BIBLE_REL, (rom_root / ROM_BIBLE_REL).is_dir()))
    present.append((ROM_GUIDE_REL, guide_shipped))
    missing = [rel for rel, ok in present if not ok]
    if missing:
        raise RuntimeError(
            f"the packaged canon under rom root {rom_root} is incomplete — missing "
            f"{sorted(set(missing))}. The rom root is populated, so this is a "
            "PACKAGING defect, not a no-rom install; refusing to launch a box with a "
            "partial canon."
        )

    # DISJOINTNESS.  ⚑ RE-ANCHORED to the BOX-HOME template root, NOT the template ROOT: a
    # root-relative walk can never match a ``canon/...`` prefix, so the guard would check
    # NOTHING while still running and passing.
    home_template_root = templates.packaged_box_home_template()
    if home_template_root is not None:
        assert_canon_bind_seed_disjoint(
            CANON_SEED_DENY_PREFIXES,
            (rel for rel, _ in templates.walk_shipped_files(home_template_root)),
        )

    out: BindArmTable = {}
    for _key, rel, _is_dir in binds:
        add_bind(
            out, "bindings.ro", _canon_dest(rel), str(rom_root / rel), "ro",
        )
    return out


def rom_agent_default_categories(
    target: "Target",
) -> BindArmTable:
    """Build the PLUGIN's bible chapter bind — the SIXTH canon bind (spec §2c).

    ⚑ Emitted by CORE from the RESOLVED *target*, NOT by the plugin and NOT through the
    agent-scope descriptor route; ``bible/agent`` is per-HARNESS, ``handbook/agent`` per-NODE.
    """
    rom_root = target.rom_root()
    if rom_root is None:
        return {}
    # ⚑ GATE: emit ONLY when the plugin actually SHIPS a chapter — an ungated bare
    # ``data/rom/`` would bind an empty dir and cost a per-launch missing-source warning.
    if not (rom_root / PLUGIN_CHAPTER_MARKER_REL).is_file():
        return {}
    out: BindArmTable = {}
    add_bind(
        out, "bindings.ro",
        _canon_dest(f"{ROM_BIBLE_REL}/{BIBLE_AGENT_CHAPTER}"), str(rom_root), "ro",
    )
    return out


# ===========================================================================
# The HANDBOOK binds + the <scope>.canon keys (spec §2c/§2b/§2d/§2g).
# ===========================================================================

# The ACTIVE-AGENT placeholder in the declarative ``canon:`` rows — a literal that cannot
# occur in a real key (``<``/``>`` are not key characters), so the substitution cannot collide.
CANON_ACTIVE_AGENT_TOKEN = "<active>"


def canon_optional_bind_keys() -> frozenset[str]:
    """The SKIP-IF-ABSENT canon bind KEYS — ``snapshot_category_entries(optional_keys=…)``."""
    from kanibako.settings.settings_resolve import normalize_bind_dest

    # ⚑ H6 — RE-DERIVED FROM THE DESTINATION (R-10/R-11), and normalized with the SAME
    # function the producer uses; matching on ``entry['key']`` would silently never hit.
    return frozenset(
        f"box.{entry['category']}.{normalize_bind_dest(str(entry['box_dest']))}"
        for entry in _canon_optional_rows()
    )


def _canon_optional_rows() -> list[Any]:
    """The ``canon:`` rows carrying ``optional: true`` — ONE filter, two views."""
    return [e for e in _load_doc().get("canon", []) if e.get("optional")]


def canon_optional_bind_dests() -> frozenset[str]:
    """The SKIP-IF-ABSENT canon binds as normalized box DESTS — the EMITTER's view."""
    from kanibako.settings.settings_resolve import normalize_bind_dest

    # ⚑ The DEST basis, not the key basis, and normalized with the SAME function that keys the
    # arm — the drift ``critical_keys`` already paid for, where a key-spelled set matched
    # NOTHING and silently degraded every entry to the default policy.
    return frozenset(
        normalize_bind_dest(str(entry["box_dest"])) for entry in _canon_optional_rows()
    )


def canon_default_categories(
    std: StandardPaths, agent_name: str | None,
) -> dict[str, object]:
    """Build the HANDBOOK binds + the agent-scope ``canon`` floor (spec §2c).

    ⚑ A MIXED table — the ``box.bindings.ro`` entries PLUS the agent scalars their ``@``-refs
    resolve against; both land in the SAME floor, so a user override of the scalar reroutes
    the bind.  ⚑⚑ The ACTIVE NODE's floor value is store-dependent (J-1 option (a)).
    """
    store_canon = f"@config.agents/{agent_name}/canon" if agent_name else None
    out: dict[str, object] = {}
    if agent_name:
        out["agent.default.canon"] = "@config.agents/default/canon"
        node_store = std.agents / agent_name / "canon"
        out[f"agent.{agent_name}.canon"] = (
            store_canon if node_store.is_dir() else "@agent.default.canon"
        )

    for entry in _load_doc().get("canon", []):
        ref = str(entry["meta_ref"])
        if CANON_ACTIVE_AGENT_TOKEN in ref:
            if not agent_name:
                continue  # NO-AGENT box: no agent tier at all, so no chapter bind.
            ref = ref.replace(CANON_ACTIVE_AGENT_TOKEN, agent_name)
        add_bind(
            out, str(entry["category"]), str(entry["box_dest"]), ref,
            str(entry["options"]),
        )
    return out


# ===========================================================================
# The box-create CANON SKELETON (J-7).
# ===========================================================================

# The handbook's chapters (BINDS: ``canon_default_categories``).  ⚑ Their MOUNTPOINTS belong to
# this one closed skeleton — creating them later would mean mkdir-ing into an already-555 tree.
HANDBOOK_CHAPTERS = ("general", "agent", "workset", "box")
HANDBOOK_CONTENTS_REL = f"{HANDBOOK_REL}/SYS_CONTENTS.md"

# ⚑⚑ THE IMPORT-FALLBACK FILES (seeds-gate F1) — per-scope chapters whose ENTRY FILE the
# skeleton pre-creates 0-byte, keyed chapter → entry filename, so ``SYS_CONTENTS.md``'s
# UNCONDITIONAL imports resolve-to-empty rather than warn on every launch.
# ⚑ ``general`` is deliberately ABSENT: a fallback there would mask a missing system handbook.
# ⚑ MACHINERY, NOT CONTENT — these live in the BOX's skeleton and are installed nowhere.
HANDBOOK_FALLBACK_ENTRIES: tuple[tuple[str, str], ...] = (
    ("agent", "SYS_AGENT.md"),
    ("workset", "SYS_WORKSET.md"),
    ("box", "SYS_BOX.md"),
)

# The directory each chapter's entry file sits in — the ``@<chapter>/directives/...``
# spelling ``SYS_CONTENTS.md`` imports.
HANDBOOK_DIRECTIVES_DIRNAME = "directives"

# ⚑⚑ THE OWNER THAT APPEARS AS ROOT INSIDE A BOX — deliberately NOT 0; under
# :data:`kanibako.runtime.container.KEEP_ID_USERNS`, ``chown 0:0`` inside ``podman unshare``
# would produce an AGENT-OWNED skeleton, the exact opposite of the intended effect.
UNSHARE_BOX_ROOT_UID = 1
UNSHARE_BOX_ROOT_GID = 1

# ⚑ TWO MODES, NOT ONE (spec J-7 banner): dirs keep the SEARCH bit so crun can traverse to the
# chapter mountpoints; file mountpoints must not be marked executable.
CANON_SKELETON_DIR_MODE = "555"
CANON_SKELETON_FILE_MODE = "444"


def canon_skeleton_rels() -> tuple[tuple[str, bool], ...]:
    """The canon skeleton as ``(home-relative posix path, is_dir)`` pairs, PARENTS-FIRST (J-7).

    ⚑ DERIVED FROM THE SAME CONSTANTS AS THE BIND DESTS, never restated — a skeleton that
    drifts from the binds is a mountpoint podman creates itself.
    ``canon/notebook`` / ``canon/workbook`` are ABSENT by design: they are SEEDED and writable.
    """
    root = CANON_GUEST_ROOT
    rels: list[tuple[str, bool]] = [
        (root, True),
        (f"{root}/{ROM_COLLECTION_REL}", False),
        (f"{root}/{ROM_BIBLE_REL}", True),
        (f"{root}/{ROM_CONTENTS_REL}", False),
    ]
    rels += [
        (f"{root}/{ROM_BIBLE_REL}/{chapter}", True)
        # ⚑ ``agent`` is ALWAYS pre-created, emission gate or not (J-7): a gate-false
        # launch must show an EMPTY root-owned mountpoint, not a missing directory.
        for chapter in (*ROM_BIBLE_CHAPTERS, BIBLE_AGENT_CHAPTER)
    ]
    rels += [
        (f"{root}/{HANDBOOK_REL}", True),
        (f"{root}/{HANDBOOK_CONTENTS_REL}", False),
    ]
    rels += [
        (f"{root}/{HANDBOOK_REL}/{chapter}", True) for chapter in HANDBOOK_CHAPTERS
    ]
    # The IMPORT-FALLBACK entry files (F1), INSIDE three of those mountpoints; their
    # ``directives/`` parents are part of the skeleton too, so nothing here is agent-creatable.
    for chapter, entry in HANDBOOK_FALLBACK_ENTRIES:
        chapter_dir = f"{root}/{HANDBOOK_REL}/{chapter}"
        rels.append((f"{chapter_dir}/{HANDBOOK_DIRECTIVES_DIRNAME}", True))
        rels.append((f"{chapter_dir}/{HANDBOOK_DIRECTIVES_DIRNAME}/{entry}", False))
    return tuple(rels)


def materialize_canon_skeleton(
    shell_path: Path,
    *,
    logger: "logging.Logger | None" = None,
    quiet: bool = False,
) -> None:
    """Create the canon SKELETON in a box home and make it root-owned + unwritable (J-7).

    ⚑ ORDER IS LOAD-BEARING: seed FIRST, protect SECOND — the 555 landing first kills the
    ``canon/{notebook,workbook}`` copies with EACCES.
    ⚑ IDEMPOTENT, BUT NOT EXTENSIBLE ONCE PROTECTED ⇒ growing the skeleton is a MIGRATION.
    ⚑ Ownership, not mode alone: a 555 dir the agent OWNS is no protection.
    """
    log = logger or _skeleton_logger()
    dirs: list[Path] = []
    files: list[Path] = []
    for rel, is_dir in canon_skeleton_rels():
        p = shell_path / rel
        try:
            if is_dir:
                p.mkdir(parents=True, exist_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.touch()
        except OSError as exc:
            log.debug("canon skeleton: could not create %s (%s)", p, exc)
            continue
        (dirs if is_dir else files).append(p)

    if not dirs and not files:
        return
    _protect_canon_skeleton(dirs, files, log, quiet=quiet)


def materialize_canon_skeleton_if_present(
    shell_path: Path, *, logger: "logging.Logger | None" = None,
) -> None:
    """Re-assert an EXISTING canon skeleton; do nothing if the home has none (helper boxes).

    ⚑ NOT :func:`materialize_canon_skeleton`: a helper home is not a box, and gaining canon
    mountpoints from a launch would be a silent layout change made by the wrong seam.
    """
    if not (shell_path / CANON_GUEST_ROOT).is_dir():
        return
    materialize_canon_skeleton(shell_path, logger=logger, quiet=True)


def _skeleton_logger() -> "logging.Logger":
    return logging.getLogger(__name__)


def _protect_canon_skeleton(
    dirs: list[Path], files: list[Path], log: "logging.Logger", *, quiet: bool = False,
) -> None:
    """Make the skeleton root-owned + unwritable from inside the user namespace.

    THREE ``podman unshare`` calls: one ``chown`` over everything, then a ``chmod`` per mode
    class.  ⚑ NEVER ``-R`` — a recursive sweep would take the SEEDED, agent-owned
    ``notebook/`` + ``workbook/`` with it.
    """
    from kanibako.runtime.container import ContainerError, ContainerRuntime

    everything = dirs + files
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        _warn_unprotected(
            everything[0], log, "no container runtime is available", True, quiet,
        )
        return

    if not runtime.unshare_chown(
        everything, UNSHARE_BOX_ROOT_UID, UNSHARE_BOX_ROOT_GID,
    ):
        _warn_unprotected(
            everything[0], log, "podman unshare chown did not succeed", True, quiet,
        )
        return
    for group, mode in ((dirs, CANON_SKELETON_DIR_MODE),
                        (files, CANON_SKELETON_FILE_MODE)):
        if group and not runtime.unshare_chmod(group, mode):
            _warn_unprotected(
                everything[0], log,
                f"podman unshare chmod {mode} did not succeed",
                False, quiet,
            )
            return
    log.debug(
        "canon skeleton protected (%d dirs + %d files under %s)",
        len(dirs), len(files), everything[0],
    )


def _warn_unprotected(
    root: Path, log: "logging.Logger", reason: str, agent_owned: bool,
    quiet: bool = False,
) -> None:
    """Report a skeleton that did not get its full lockdown.

    ⚑ *quiet* demotes the report to DEBUG; the POST-START caller sets it, because at WARNING
    it would paint over the live session on every launch of a docker/unshare-less host.
    ⚑ The two arms are genuinely different and must not share wording.
    """
    emit = log.debug if quiet else log.warning
    if agent_owned:
        emit(
            "canon books at %s are left writable from inside the box (%s). The box "
            "works normally — every canon bind still lands on its mountpoint — but "
            "the agent can create stray files under ~/canon instead of only under "
            "~/canon/{notebook,workbook}.",
            root, reason,
        )
    else:
        emit(
            "canon books at %s are root-owned but keep their default modes (%s). The "
            "agent still cannot write them, and the box works normally; only the "
            "declared 555/444 modes were not applied.",
            root, reason,
        )


def _table_bind_dests(table: str) -> frozenset[str]:
    """The normalized box DESTS one declarative bind *table* names.

    Read from the SAME rows that declare the binds and normalized with the SAME
    function that keys the emitter's map — the :func:`canon_optional_bind_dests`
    pattern, for the same reason: a dest spelled twice is a dest that can drift.
    """
    from kanibako.settings.settings_resolve import normalize_bind_dest

    return frozenset(
        normalize_bind_dest(str(entry["box_dest"]))
        for entry in _load_doc().get(table, [])
    )


#: The declarative BIND tables, in file order — the sections whose rows carry a
#: ``box_dest``.  ⚑ NOT every top-level section: ``masks``, ``agent_default`` and ``env``
#: are the file's non-bind scalar tables and declare no destination.
BIND_TABLES = ("channels", "core", "kani", "kickoff", "canon", "helpers", "images")


def bind_dest_families() -> dict[str, str]:
    """``{box_dest: table}`` — WHICH declarative section of this file declares a dest.

    The provenance read behind ``kanibako system defaults``
    (:mod:`kanibako.settings.defaults_inventory`): a dest is reported as declared by the
    section it actually sits in, so one that moves between tables re-labels itself
    instead of drifting away from a hand-written label.

    ⚑ Dests are returned AS WRITTEN, not normalized — the caller matches them against the
    manifest's ``bind_default_entries`` keys, which are the same ``~``-spelled strings.
    :func:`_table_bind_dests` normalizes because its callers filter an EMITTED map, whose
    keys have been through ``normalize_bind_dest``; these two reads answer different
    questions and must not be merged.
    """
    return {
        str(entry["box_dest"]): table
        for table in BIND_TABLES
        for entry in _load_doc().get(table) or []
    }


def helper_bind_dests() -> frozenset[str]:
    """The HELPER table's own dests — the helper-hub resolve's EMISSION filter.

    A narrow resolve emits ONLY the dests its own injected table declares
    (``commands.start._narrow_bind_map``); everything the user's cascade puts
    elsewhere belongs to the main path, which emits it from the collapse.
    """
    return _table_bind_dests("helpers")


def image_bind_dests() -> frozenset[str]:
    """The IMAGE table's own dests — the image-sharing resolve's EMISSION filter."""
    return _table_bind_dests("images")


def helper_default_categories(
    *,
    socket_path: Path,
    log_path: Path,
) -> BindArmTable:
    """Build the helper hub binds — the live unix SOCKET + the per-box message LOG (Phase B).

    ⚠ ``helper_sock`` options MUST be ``""``: a ``Z``/``U`` relabel/chown would break the
    shared socket topology of a LIVE unix socket the hub listens on.
    ⚑ The dests carry no ``$XDG_STATE_HOME`` token — they are written into the runtime's
    arguments BEFORE the box is live; ``box_supervisor.project_pinned_xdg`` restores XDG later.
    """
    sources: dict[str, Path] = {
        # symbolic source name -> probed host source (the DEST is in the file)
        "helper_sock": socket_path,
        "helper_log": log_path,
    }

    binds: BindArmTable = {}
    for entry in _load_doc().get("helpers", []):
        src_path = sources[entry["source"]]
        # Skip-if-missing gate (parity with the old `.exists()`-guarded appends).
        if not src_path.exists():
            continue
        box_dest = str(entry["box_dest"])
        category = entry["category"]
        # ⚑ B2b: the ``.exists()`` gate above keys off the PROBED path while ``helper_log``'s
        # emitted host_src is the spec FORMULA, so a user repointing ``workset.logs`` moves the
        # MOUNT but not the hub's WRITER — see migration M-14.  ⚑ ``helper_sock`` is NOT
        # routed: its hashed, length-bounded socket name has no spec spelling (JC-B2b-3).
        host_src = entry.get("meta_ref", str(src_path))
        add_bind(binds, category, box_dest, host_src, str(entry["options"]))
    return binds


def image_default_categories(
    *,
    graph_root: Path | None,
    storage_conf_path: Path,
) -> dict[str, object]:
    """Build the image-sharing binds as ``default_categories`` (Phase B, D-M8).

    ⚑ B3: the store bind is ROUTED THROUGH THE USER KEY ``@box.images_store``, whose DEFAULT is
    the probed *graph_root* — a floor scalar in this same MIXED table.  ⚑ 11a: the probe feeds
    ONLY that default, so ``graph_root=None`` emits no scalar rather than gating the table.
    ``images_conf`` stays an INTERNAL bind and NOT a key (spec §0's test).
    """
    sources: dict[str, str] = {
        "images_conf": str(storage_conf_path),
    }

    binds: dict[str, object] = {}
    if graph_root is not None:
        sources["images_store"] = str(graph_root)
        # The USER KEY behind the store bind: the probe lands as ``box.images_store``'s DEFAULT.
        binds["box.images_store"] = str(graph_root)
    for entry in _load_doc().get("images", []):
        category = entry["category"]
        # ⚑ ``meta_ref`` is the emitted host_src; the symbolic ``source`` is the probed-literal
        # fallback, read LAZILY so a probe-fail ``None`` only raises if an entry needs it.
        host_src = entry.get("meta_ref")
        if host_src is None:
            host_src = sources[entry["source"]]
        add_bind(
            binds, category, str(entry["box_dest"]), host_src,
            str(entry["options"]),
        )
    return binds
