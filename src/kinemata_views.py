"""Predicates `kinemata.toml` names in a `[[registry]] where`, to narrow a view,
and the helpers its `[[parity]]` oracles import (see ORACLE HELPERS, below).

🛑 THIS IS NOT PRODUCT CODE AND IT IS NOT A STRAY. It is deliberately tracked, it
is deliberately OUTSIDE `kanibako/`, and it is deliberately HERE rather than in
`tests/support/` beside `filenames.py` and `protected_trees.py`, which is where a
reader would look for it first and where the integration plan said to put it.
Measured 2026-09-15 under kinemata `0.1.0`, by running it:

    kinemata parity -c <probe>
    error: [[registry]] 'standalone-absences' where cannot be evaluated: cannot
    import 'tests.support.views' for target 'tests.support.views:standalone_is_absent':
    ModuleNotFoundError: No module named 'tests'          (rc=2)

⚑ THE REASON IS THE IMPORTER, NOT THE LAYOUT. A `where` predicate is resolved by
the interpreter running kinemata, at CONFIG LOAD -- so a target that will not
import does not fail one check, it refuses the config for `check`, `claims`,
`parity` and `shape` alike. kinemata puts NOTHING on `sys.path` on a project's
behalf and says so in three places (`docs/design.md`, `README.md`,
`docs/introduction.md`). Under the console script `sys.path[0]` is the script's
own directory, never the working directory, so the repository ROOT is not on the
path and `tests.` is not importable. The ONE project directory that IS on the
path is this one: `pip install -e .` writes a `.pth` naming `src/`, which is why
the CI conformance job installs the project at all (its own comment says the
parity oracle imports it).

⚑ IT IS NOT SHIPPED, AND THAT IS MEASURED RATHER THAN INTENDED.
`[tool.setuptools.packages.find] where = ["src"]` finds PACKAGES, and a top-level
module is included only through `py-modules`, which this project does not declare.
Built 2026-09-15 with `python -m build --wheel --no-isolation`: the wheel holds 191
entries, top-level `kanibako` and `kanibako_cli-1.8.0.dist-info` alone, and no
`kinemata_views`.

🛑 WHAT IT COSTS, STATED SO THE NEXT PERSON DOES NOT FIND IT THE HARD WAY: this
module is reachable only under setuptools' DEFAULT (`.pth`) editable install, and
"editable" alone is not the condition. Measured 2026-09-15: a clean-venv
`pip install -e .` on a tree already holding this file writes the `.pth` naming
`src/` and the import succeeds from any cwd -- which is the install the CI
conformance job does. `pip install -e . --config-settings editable_mode=strict`
exposes the DECLARED PACKAGES only, so `kanibako` imports and this module does
not. Under strict editable and under a non-editable install alike, this file is
neither on the path nor in the wheel, and every kinemata command exits 2 naming
this target. That failure is LOUD and names the module, which is why it is an
acceptable trade -- but the only home that survives either is inside the shipped
package, and putting a checker's predicates into every user's wheel is a
decision, not a fix.

⚑ THE NAME IS DISTINCTIVE ON PURPOSE. Upstream's own draft spells the target
`views:bind_shaped`; a top-level module called `views` would shadow, for everyone
holding an editable install of this project, any other top-level `views` on their
path. `kinemata_views` cannot, and it says at the import site which tool reads it.

⚑ WHAT A PREDICATE RECEIVES is one `kinemata.contract.Entry` and nothing else --
no registry, no config, no tree. `entry.id` is the manifest row's key and
`entry.extra` is the row's own fields. Typed loosely below because kinemata is not
a dependency of this project and must not become one: it is pinned in CI, not in
`pyproject.toml`.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any


def _standalone_arm(entry: Any) -> tuple[bool, Any]:
    """`(has_arm, value)` for the `standalone` arm of the row's per-mode map.

    ⚑ THE MAP IS IN `default:` ON A SETTABLE ROW AND IN `value:` ON A DERIVED `meta.*`
    ROW. No manifest row carries both, so reading one and falling back to the other
    selects every per-mode row -- `meta.box.share_workset` and
    `meta.box.auth.workset_path` declare `standalone: null` in `value:`.

    ⚑ THE `standalone` KEY MUST BE PRESENT, and that is the half a declarative
    guard cannot express. `at_path` returns MISSING for a row with no `default:`
    map at all, `_value` renders MISSING as `None`, and a `matches` guard would
    therefore sweep in every row that never had an arm to declare. Two questions
    -- *is there an arm* and *what does it hold* -- and a `where` guard claims one.
    """
    arm = entry.extra.get("default")
    if arm is None:
        arm = entry.extra.get("value")
    if not isinstance(arm, dict) or "standalone" not in arm:
        return False, None
    return True, arm["standalone"]


def standalone_is_null(entry: Any) -> bool:
    """The row's per-mode map declares its `standalone` arm as `<None>` -- a YAML
    `null`, which is a VALUE the standalone floor must SUPPLY, never omit ([R177]).
    """
    has_arm, value = _standalone_arm(entry)
    return has_arm and value is None


def standalone_is_placeholder(entry: Any) -> bool:
    """The row's per-mode map declares its `standalone` arm as a `<…>` PROSE
    placeholder (`workset.kuid` reads `<generated at creation>`) -- not a value, so
    no floor may emit one.
    """
    has_arm, value = _standalone_arm(entry)
    return (
        has_arm and isinstance(value, str)
        and value.startswith("<") and value.endswith(">")
    )


def never_settable_path(entry: Any) -> bool:
    """A `type: path` row with no set route -- `set: never`.

    ⚑ BOTH CELLS, DELIBERATELY. `set: never` alone selects 32 rows and would
    assert something wider than the test this mirrors
    (`TestThePathTypeColumnHasOneCodeCarrier`), which splits the path rows by
    `set:` and claims each half separately. Over-claiming here would red on a
    row the manifest never promised anything about.
    """
    return entry.extra.get("set") == "never" and entry.extra.get("type") == "path"


#: The delivery families whose keys are not ordinary scalar settings, spelled at
#: the segment a key carries the family in. `bindings.ro` and `bindings.rw` share
#: the head `bindings`, so the NINE families occupy EIGHT heads.
#:
#: 🛑 ALL NINE ARE HERE, FOR TWO DIFFERENT REASONS. The seven BIND-SHAPED families
#: are here because the key ends at the family and the destination is the sub-key,
#: so no spelling of one belongs in a set of scalar keys. `env` and `secret_path`
#: are here because they are the two `<VAR>`-PARAMETRIC SCALAR families -- the
#: spec's "only name-parametric categories left" -- whose keys are spelled
#: `<scope>.env.<VAR>` and `<scope>.secret_path.<VAR>` and are answered by
#: `config_keys._is_scope_env_key` / `_is_scope_secret_key`, two branches of
#: `is_known_key`, never by the set.
#:
#: ⚑ CLAIM EITHER ONE HERE AND THE VIEW REDS A ROW THE SET HAS NO REASON TO HOLD.
#: Measured 2026-09-19: dropping `env` admits the manifest's illustrative
#: `box.env.COLORTERM` and reports 50 declared, the extra row produced by nothing.
#: `secret_path` has no manifest row yet, so the count is unchanged either way; it
#: is listed because the realistic future row is that row's exact analog --
#: `is_known_key("box.secret_path")` is False, `is_known_key("box.secret_path.FOO")`
#: is True -- and with it this set is exactly the manifest's own two shape classes.
_DELIVERY_HEADS = frozenset(
    {
        "bindings",
        "masks",
        "caches",
        "seeded",
        "synced",
        "common",
        "env",
        "secret_path",
    }
)

#: The four scopes a FIXED, CLI-addressable key is spelled under. `agent` is
#: absent because the CLI spells its leaves BARE (`model`, not
#: `agent.default.model`), so the `agent.default.*` rows that survive the other
#: cells have no dotted spelling any set could match; `test_agent_leaf_shape.py`
#: claims that half. `meta` is absent because the `meta.*` contract is read-only.
_FIXED_SCOPES = frozenset({"config", "system", "workset", "box"})


def fixed_scope_key(entry: Any) -> bool:
    """A declared key with a FIXED spelling that the CLI must recognize.

    Five conditions, and each one removes rows the `KNOWN_CONFIG_KEYS`
    disambiguation set has no reason to hold: a `set: never` row has no write
    route, a `parametric` row and a row carrying a `<…>` segment have no
    spelling any set could contain, a row outside the four scopes is either an
    agent leaf (bare, so not a dotted spelling at all) or `meta.*` (read-only),
    and a row under a DELIVERY-FAMILY head is either a sub-key destination or a
    `<VAR>`-parametric spelling -- `_DELIVERY_HEADS` carries which is which, and
    `box.masks`, `box.bindings.{ro,rw}` and `box.env.COLORTERM` are the
    manifest's illustrative rows for those families.

    🛑 THE FAMILY TEST IS POSITIONAL -- `parts[1]`, NEVER `any(part in …)`.
    `common` is BOTH a delivery family and a `channels` leaf, so testing every
    segment silently drops `system.channels.common` and `workset.channels.common`
    -- two rows that are ordinary settable path keys and are in the set.
    ⚑ RUN, NOT REASONED, 2026-09-19: the wide spelling reports `47 declared`
    where this one reports 49, AND IT STILL EXITS 0. That is the cost worth
    naming -- a selector does not fail on a row it drops, so the wide test buys
    a green over two rows nobody is checking any more.
    """
    if entry.extra.get("set") == "never" or entry.extra.get("parametric"):
        return False
    parts = str(entry.id).split(".")
    if any(part.startswith("<") for part in parts):
        return False
    if parts[0] not in _FIXED_SCOPES:
        return False
    return not (len(parts) > 1 and parts[1] in _DELIVERY_HEADS)


#: The three kinds `config_keys.KEY_TYPES` carries -- its own header names them
#: (`bool`, `int`, `path`) and says `access` is an ENUM guarded elsewhere, never a
#: type there. A `str` or `version_marker` row is not coerced by the CLI, so it has
#: no reason to be in that table.
#: `kinemata.toml`'s `key-kinds` registry declares this vocabulary; this set is the
#: selector's copy, and `key-types` reds if the two part.
_CLI_TYPED = frozenset({"bool", "int", "path"})


def cli_typed_key(entry: Any) -> bool:
    """A FIXED-scope key whose declared `type:` is one the CLI acts on.

    `fixed_scope_key` AND a `type:` in `_CLI_TYPED` -- the manifest-side statement
    of what `KEY_TYPES` says it is: *"the DECLARED TYPE of every key whose type
    the CLI acts on"*, fixed spellings only (its parametric path keys are
    answered by `is_path_valued_key`, never by the table).

    ⚑ THE GUARD READS `type:`, WHICH IS ALSO THE CELL THE PARITY COMPARES, and the
    consequence is stated rather than hidden: a code type that disagrees with a
    `bool`/`int`/`path` row is a VALUE divergence, but a code entry for a row whose
    type is outside the three (`str`, say) falls OUT of this view and is reported
    as a MEMBERSHIP finding -- `produced, declared by nothing`. Both are red; they
    differ only in which line names the row.
    """
    return fixed_scope_key(entry) and entry.extra.get("type") in _CLI_TYPED


def cli_routed_key(entry: Any) -> bool:
    """A FIXED-scope key the CLI may set -- `set: cli+file`.

    `fixed_scope_key` AND `set: cli+file`. The six `config.*` rows are
    `set: file`, and are the only fixed-scope rows this drops.

    ⚑ THIS IS THE CONVERSE `TestSetColumnConformance` DECLINES, AND ITS REASON
    DOES NOT REACH THIS VIEW. The test says `cli+file => in _KEY_ROUTES` is the
    wrong shape because the `agent.*` tier and the bare any-agent keys are written
    through their own routes. `fixed_scope_key` has already excluded both -- no
    `agent` scope, no `<…>` segment -- so over the fixed scopes the routing table
    is the one CLI write route, and a `cli+file` row it does not route is a key
    the CLI promises and cannot set.

    The `env`/`secret_path` `<VAR>` rows, which `_DELIVERY_HEADS` removes, are the
    third route (`_is_scope_env_key` / `_is_scope_secret_key`).
    """
    return fixed_scope_key(entry) and entry.extra.get("set") == "cli+file"


def bootstrap_path_row(entry: Any) -> bool:
    """A `type: path` row in the two BOOTSTRAP scopes, `config` and `system`.

    The manifest-side statement of the corpus `settings/bootstrap.py` carries in
    `CONFIG_PATH_DEFAULTS` and `SYSTEM_PATH_DEFAULTS`: every path key of the two
    tiers that resolve before any workset exists. No `set:` condition, because the
    six `config.*` rows are `set: file` and belong to the corpus all the same.
    """
    return (
        str(entry.id).split(".", 1)[0] in {"config", "system"}
        and entry.extra.get("type") == "path"
    )


# ---------------------------------------------------------------------------
# ORACLE HELPERS. Not `where` predicates: the functions below are imported by
# `[[parity]]` COMMANDS, which run as a subprocess of kinemata with this module
# on the path for the reason the header gives. They are here, once, because
# several oracles need each of them and an inline copy per command is the
# duplication `check` exists to find.
# ⚑ Each imports kanibako INSIDE the function. A module-level import would run
# at CONFIG LOAD, where the `where` predicates above are resolved, so a kanibako
# that failed to import would refuse the config for every command.


def every_mode_cell(floors: Any) -> dict[str, Any]:
    """`{key: cell}` in the manifest's own notation, from one floor per box mode.

    *floors* maps each mode to the floor a builder returned for it. A key the
    floor gives ONE value in every mode yields that value bare; a key whose modes
    differ yields the `{mode: value}` map. That is how the manifest writes a
    `default:` or `value:` cell -- a uniform row is a scalar, a mode-keyed row a
    map, and a `null` arm is a value -- so the oracle's JSON and the declared cell
    are compared as data, with no translation. (Before kinemata 0.5.0, `format = "json"`
    refused a `translate`, so this step could not be declared on the manifest side;
    the per-mode map and its scalar collapse still need this function.)
    It fails closed: a manifest map whose three arms are equal reds as a scalar.

    🛑 A KEY SOME MODE OMITS IS LEFT OUT, because no cell can spell an omitted
    arm: the manifest writes a `<…>` placeholder there (`workset.kuid`'s
    `<generated at creation>`) or the arm is a caller's input the floor was not
    handed. Each view that prints through this says which keys that drops and
    which declaration holds them instead.
    """
    modes = sorted(floors)
    common = set.intersection(*(set(floors[mode]) for mode in modes))
    cells: dict[str, Any] = {}
    for key in sorted(common):
        arms = {mode: floors[mode][key] for mode in modes}
        values = list(arms.values())
        cells[key] = values[0] if all(v == values[0] for v in values) else arms
    return cells


def literal_text(value: Any) -> Any:
    """The text a floor's `literal_expr` value spells; a non-string passes through.

    A floor enters a resolved host path as `settings_resolve.literal_expr`, so a
    sentinel `@`-ref handed in comes back escaped; this reads it back to the path the
    manifest formula names. A string that is NOT exactly a `literal_expr` raises, so a
    producer that stops escaping its path reds the view instead of printing the cell.
    """
    from kanibako.settings.settings_resolve import literal_expr

    if not isinstance(value, str):
        return value
    # Inverts both of `literal_expr`'s marks: a backslash escape and a doubled brace.
    text = re.sub(
        r"\\(.)|\{\{|\}\}", lambda m: m.group(1) or m.group(0)[0], value, flags=re.DOTALL,
    )
    if literal_expr(text) != value:
        raise ValueError(f"not a literal floor value: {value!r}")
    return text


#: The workset root a sentinel run hands the derivations, as the `@`-ref the
#: manifest composes from. A derivation joins its output onto this exactly as it
#: would onto a real path, so what it prints is the manifest's formula when the
#: composition is the declared one -- the code does the composing, and no
#: resolver is written here.
REF_WORKSET_PATH = "@meta.workset.path"

#: The attribute that carries the workset root in each mode -- the E6 row
#: `meta.runtime.ws_root`: PRIMARY `@config.primary_workset` (`std.primary_workset`),
#: NAMED the detected workset root (`proj.group.root`), STANDALONE the project dir
#: (`proj.metadata_path`). Only that attribute carries `REF_WORKSET_PATH`; every
#: other candidate carries a DECOY naming itself -- or, for the group outside
#: NAMED, is absent and a read BLOCKs -- so a derivation that reads the wrong root
#: for a mode reds.
_ROOT_ATTRIBUTE = {
    "primary": "primary_workset",
    "named": "group_root",
    "standalone": "metadata_path",
}


def _root_or_decoy(mode: str, attribute: str) -> Any:
    from pathlib import Path

    if _ROOT_ATTRIBUTE[mode] == attribute:
        return Path(REF_WORKSET_PATH)
    return Path(f"@DECOY.{attribute}")


def ref_token_project(mode: str, *, workset_name: str, box_name: str) -> Any:
    """The `ProjectPaths` attributes the channel and helper derivations read.

    *workset_name* and *box_name* are the `@`-refs to hand in, spelled as the
    cell under comparison spells them: bare where the ref ends the cell
    (`@meta.workset.name`), braced where it is embedded (`@{meta.workset.name}`,
    `policy.reference_forms`). The caller chooses, because one cell cannot be
    matched by the other spelling.

    ⚑ MODE-AWARE: `metadata_path` and the named group's `root` carry the workset
    ref only in the mode whose root they are (`_ROOT_ATTRIBUTE`), a decoy
    otherwise. Only a NAMED box has a group, as in the product.
    ⚑ ONLY THE NAMED MODE CARRIES *workset_name*. `channels.workset_name_token`
    answers PRIMARY and STANDALONE with the constant tokens `__PRIMARY__` /
    `__STANDALONE__` -- the values `meta.workset.name` resolves to there -- so no
    input can make those two modes print the ref.
    """
    from types import SimpleNamespace

    from kanibako.settings.paths import BoxMode

    box_mode = BoxMode(mode)
    group = None
    if box_mode is BoxMode.named:
        group = SimpleNamespace(
            name=workset_name, root=_root_or_decoy(mode, "group_root"),
        )
    return SimpleNamespace(
        mode=box_mode, group=group, name=box_name,
        metadata_path=_root_or_decoy(mode, "metadata_path"),
    )


def ref_token_standard_paths(mode: str) -> Any:
    """The `StandardPaths` attributes the channel derivations read, as `@`-refs.

    `primary_workset` is the workset ref only for a PRIMARY box and a decoy
    otherwise (`_ROOT_ATTRIBUTE`). Deliberately NOT a `StandardPaths`:
    constructing one probes the host's XDG environment, and a sentinel run is
    about a composition, not about the host.

    `early_system` is the early-system record the workset-key readers take, built
    by the product's own pure builder over an EMPTY tier (no system file states a
    `workset.*` repoint) and a resolved tier whose every path is its own `@`-ref.
    """
    from pathlib import Path
    from types import SimpleNamespace

    from kanibako.settings.bootstrap import SYSTEM_PATH_DEFAULTS
    from kanibako.settings.workset_dirkeys import early_system

    resolved = {key: Path(f"@{key}") for key in (*SYSTEM_PATH_DEFAULTS, "config.settings")}
    return SimpleNamespace(
        primary_workset=_root_or_decoy(mode, "primary_workset"),
        channels_common=Path("@system.channels.common"),
        channels_chat=Path("@system.channels.chat"),
        channels_mailboxes=Path("@system.channels.mailboxes"),
        channels_share=Path("@system.channels.share"),
        early_system=early_system({}, resolved),
    )


def guest_bind_arm(binds: Any, arm: str) -> list[tuple[str, tuple[str, ...]]]:
    """`(dest, entry)` for each entry of *binds*' `box.<arm>` arm, sorted by dest.

    A bind emitter keys its arm by GUEST path (`settings_resolve.normalize_bind_dest`),
    while `bind_default_entries` keys the same row `~/...`; each dest under
    `GUEST_HOME` is written back as the `~` the manifest keys on, importing the
    constant rather than respelling it.
    """
    from kanibako.settings.settings_resolve import GUEST_HOME

    out = []
    for dest, entry in (binds.get(f"box.{arm}") or {}).items():
        if dest.startswith(GUEST_HOME + "/"):
            dest = "~" + dest[len(GUEST_HOME):]
        out.append((dest, tuple(entry)))
    return sorted(out)


def sentinel_helper_binds() -> Any:
    """`core_defaults.helper_default_categories` fed the socket `helper_socket_path`
    names for a NAMED sentinel box, under a run dir spelled `@system.runtime`.

    The box and workset names are the braced refs the socket cell embeds
    (`@{meta.box.name}`, `@{meta.workset.name}`). Both sources are created, because
    the emitter binds only a source that exists, and they are created RELATIVE to
    the working directory -- so the caller runs this in a scratch cwd.
    """
    from pathlib import Path

    from kanibako.commands.start import helper_socket_path
    from kanibako.settings.core_defaults import helper_default_categories

    run_dir = Path("@system.runtime")
    run_dir.mkdir()
    socket = helper_socket_path(
        ref_token_project(
            "named", workset_name="@{meta.workset.name}", box_name="@{meta.box.name}",
        ),
        run_dir,
    )
    socket.touch()
    log = Path("helpers.jsonl")
    log.touch()
    return helper_default_categories(socket_path=socket, log_path=log)


def box_address_floor(mode: str) -> dict[str, Any]:
    """`meta_identity_floor` fed `channels.box_channel_addresses` for a *mode* sentinel
    box through `settings_launch.box_address_args` -- the wiring `commands.start`
    unpacks into the same call, so the three address slots are the launch's own.

    The box name is `@meta.box.name` and the named workset's `@meta.workset.name`
    (bare: each ref ends its cell). The derivation reads a workset file at the stub
    root, so the caller runs this in a scratch cwd, where `@meta.workset.path/...`
    is absent. The other identity inputs are decoys naming themselves.
    """
    from kanibako.channels.channels import box_channel_addresses
    from kanibako.settings.settings_launch import box_address_args, meta_identity_floor

    addr = box_channel_addresses(
        ref_token_project(
            mode, workset_name="@meta.workset.name", box_name="@meta.box.name",
        ),
        ref_token_standard_paths(mode),
    )
    return meta_identity_floor(
        box_name="@meta.box.name", project_path="@DECOY.project_path",
        **box_address_args(addr), box_settings="@DECOY.box_settings",
    )


@contextlib.contextmanager
def recording_launch_snapshots() -> Any:
    """Record every snapshot ``build_launch_snapshot`` produces while the block runs.

    ⚑ THE ONE MECHANISM THAT KEEPS A PROBE FROM BECOMING A SECOND CARRIER.  Every
    resolve on the launch path funnels through this single builder — ``start.py`` calls
    it as ``settings_launch.build_launch_snapshot`` and ``config.py`` imports it inside
    the function body, so both bind the module attribute at CALL time and both are seen.
    A probe therefore never has to know what floor a resolve assembles or what
    arguments it forwards: it calls the production function and collects what that
    function's own pipeline built.  Each record is ``(snapshot, cli_level)``: the §1A
    CLI level the resolve was handed rides along, because a leaf it supplied is the
    launch's INPUT, not a default.
    """
    from kanibako.settings import settings_launch

    built: list[tuple[Any, Any]] = []
    real = settings_launch.build_launch_snapshot

    def spy(*args: Any, **kwargs: Any) -> Any:
        snapshot = real(*args, **kwargs)
        built.append((snapshot, kwargs.get("cli_level")))
        return snapshot

    settings_launch.build_launch_snapshot = spy
    try:
        yield built
    finally:
        settings_launch.build_launch_snapshot = real


def existing_box_termini(
    std: Any, config_file: Any, proj: Any, target: Any, node: str,
) -> list[tuple[str, Any, Any]]:
    """Every terminus the production path produces FOR A BOX THAT ALREADY EXISTS.

    Returns ``[(label, snapshot, cli_level), …]``; the label names the production entry
    point and its call site, so a finding can say WHERE a key answered.

    ⚑ IN-SCOPE IS DECIDED BY THE CALL SITE (``test_reachability_conformance``'s module
    docstring): a resolve counts when the production path reaches it down a route that
    is NOT gated on the box being created.  Each driver below therefore carries the
    existing-box call site it stands for.  Nothing is skipped by name — the
    create-time resolves have no line here because no existing-box route reaches
    them, not because they were filtered out.

    ⚑ ARGUMENT SHAPE HELD CONSTANT: ``system_settings_path`` / ``agent_cfg_path`` are
    passed ``None`` throughout (an absent file is an empty tier, which the resolvers
    document as an ordinary state).  *node* is the agent node every driver launches.

    ⚑ NOTHING IS SWALLOWED.  A driver that cannot stand up raises and reds the run; a
    ``try``/``except`` here would silently drop a terminus, which is a carve-out wearing
    an exception handler.
    """
    from kanibako.commands import start as start_cmd
    from kanibako.settings.agent_select import AgentSelection
    from kanibako.settings.settings_launch import load_merged_config
    from kanibako.settings.paths import box_workset_settings_paths

    box_path, workset_path = box_workset_settings_paths(proj)
    # The §1A selection level a launch installs, built from the PRODUCTION dataclass
    # rather than hand-spelled — ``AgentSelection.selection_level`` is the only thing
    # that knows the shape (``{system.agent: node}``) and the no-agent ``None``.
    selection = AgentSelection(node=node, source="settings").selection_level

    collected: list[tuple[str, Any, Any]] = []
    with recording_launch_snapshots() as built:

        def drive(label: str, call: Any) -> None:
            start_at = len(built)
            call()
            for offset, (snapshot, cli_level) in enumerate(built[start_at:]):
                collected.append((f"{label}#{offset}", snapshot, cli_level))

        # _run_container — every launch loads the merged config before anything else,
        # and its box-scalar resolve (settings_launch.resolve_box_scalars) is a real resolve.
        drive("load_merged_config", lambda: load_merged_config(
            box_path, workset_path=workset_path, cli_overrides=None,
        ))
        # _run_container's _bootstrap_choice / _effective_transform — the two focused
        # agent-behavior resolves (_agent_scalar_pick) a launch runs ahead of the main
        # snapshot.
        drive("bootstrap_choice", lambda: start_cmd._bootstrap_choice(
            proj, None, node, std=std, selection_level=selection, agent_path=None,
        ))
        drive("effective_transform", lambda: start_cmd._effective_transform(
            proj, None, node, target, None, std=std, selection_level=selection,
        ))
        # _run_container's _resolve_box_launch_decisions — the auth/decisions resolve.
        drive("box_launch_decisions", lambda: start_cmd._resolve_box_launch_decisions(
            std=std, proj=proj, target=target, agent_name=node, agent_cfg=None,
            system_settings_path=None, agent_cfg_path=None, selection_level=selection,
        ))
        # stop.py's and launch/creds_watcher.py's calls to start._resolve_box_auth_source — the
        # same build for the TARGET-LESS paths.  An existing box is what both of those act on,
        # which is the whole test.
        drive("box_auth_source", lambda: start_cmd._resolve_box_auth_source(
            std=std, proj=proj, agent_name=node,
            system_settings_path=None, agent_cfg_path=None, selection_level=selection,
        ))
        # _run_container's _resolve_launch_snapshot — the main launch resolve, carrying
        # the selection the launch installs (no flag is set, so the level is that alone).
        drive("launch_snapshot", lambda: start_cmd._resolve_launch_snapshot(
            std=std, proj=proj, agent_name=node,
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=target, agent_cfg=None,
            cli_level=selection,
        ))
    return collected


def fresh_launch_snapshots() -> Any:
    """Yield `(node, mode, terminus, snapshot, cli_level)` for every terminus
    (:func:`existing_box_termini`) of a freshly initialized box, for every mode and
    every agent node discovery finds -- the launch's own floor assembly, with nothing
    on top of it but what `init` writes.

    ⚑ IT TAKES OVER THE PROCESS'S HOME: `HOME`, the four `XDG_*` bases and
    `XDG_RUNTIME_DIR` point into a scratch tree, and the cwd moves there, before any
    path is loaded. An oracle is its own process, which is the only reason that is
    acceptable. The three boxes are the shapes the reachability probe
    (`test_reachability_conformance`) stands up: a primary project, a project in a
    named workset, and a standalone project, each in its own directory.
    ⚑ THE NODES ARE `targets.discover_targets()`'s, so an installed plugin is covered
    without being named here -- and so the run means what it says only where the
    plugins are installed (the CI `conformance` job installs all three).
    """
    import os
    import tempfile
    from pathlib import Path

    # The XDG names are `bootstrap`'s declared constants; the module is a terminal leaf
    # that reads no environment, so importing it before the takeover loads no path.
    from kanibako.settings.bootstrap import (
        XDG_CACHE_HOME, XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_RUNTIME_DIR, XDG_STATE_HOME,
    )

    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        env = {
            "HOME": "home", XDG_CONFIG_HOME: "config", XDG_DATA_HOME: "data",
            XDG_STATE_HOME: "state", XDG_CACHE_HOME: "cache", XDG_RUNTIME_DIR: "runtime",
        }
        for var, sub in env.items():
            (root / sub).mkdir()
            os.environ[var] = str(root / sub)
        os.chdir(root)

        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config import (
            load_config, user_config_file, write_global_config,
        )
        from kanibako.settings.paths import (
            WorksetSpec, load_std_paths, resolve_project, resolve_standalone_project,
            resolve_workset_project,
        )
        from kanibako.targets import discover_targets, resolve_target

        write_global_config(user_config_file())
        config = load_config(user_config_file())
        std = load_std_paths(config)
        for sub in ("primary-project", "named-source", "standalone-project"):
            (root / sub).mkdir()
        workset = create_workset("floor-probe", root / "worksets" / "floor-probe", std)
        add_project(workset, "named-project", root / "named-source")
        projects = {
            "primary": resolve_project(
                std, config, str(root / "primary-project"), initialize=True,
            ),
            "named": resolve_workset_project(
                WorksetSpec.from_workset(workset), "named-project", std, config,
                initialize=True,
            ),
            "standalone": resolve_standalone_project(
                std, config, str(root / "standalone-project"), initialize=True,
            ),
        }
        for node in sorted(discover_targets()):
            for mode, proj in projects.items():
                termini = existing_box_termini(
                    std, user_config_file(), proj, resolve_target(node, None), node,
                )
                for terminus, snapshot, cli_level in termini:
                    yield node, mode, terminus, snapshot, cli_level


def snapshot_paths(snapshot: Any) -> Any:
    """Yield `(dotted path, value)` for every path of a `KeyStore` snapshot, NODES
    included, walked by the production `settings_keyspace.walk_store_paths`.

    ⚑ A node is yielded too: a `<None>` row that resolved to a table has a value.
    """
    from kanibako.settings.settings_keyspace import walk_store_paths

    for segments, _is_node in walk_store_paths(snapshot):
        value = snapshot
        for segment in segments:
            value = dict.__getitem__(value, segment)
        yield ".".join(segments), value


def declares_no_floor_value(entry: Any) -> bool:
    """A `keys:` row whose `default:` is `<None>` -- a YAML `null`, "there is no
    value". The launch must RESOLVE NONE for it, on every tier.

    ⚑ `null` ONLY, NOT a `<...>` PROSE placeholder: `box.images_store`'s
    `<runtime-probed podman graphroot>` describes a value the image floor supplies by
    design (`test_manifest_conformance`'s E2). ⚑ A row floored as a PRESENT `None`
    ([R177]; `default-tier-none`, `shell-tier-fence`, `shell-template-none`,
    `box-scalar-floor`) is selected too: those views compare its own carrier, and
    this one asks every floor at once.
    """
    return "default" in entry.extra and entry.extra["default"] is None


def system_value_row(entry: Any) -> bool:
    """A `system.*` key row whose `default:` is a value, not `null` -- the rows the
    keyspace spec's §2g table states a comparable default for."""
    return entry.id.startswith("system.") and entry.extra.get("default") is not None


# DESIGN § 7: the construct-time spellings, written at the instance's own level at create.
_CONSTRUCT_TIME = frozenset({
    "<generated at creation>", "<construct-time>", "<the user's real project dir>",
})
_AGENT = "anyagent"


def _mode_arm(row: Any, mode: str) -> Any:
    """*row*'s `default:` (else `value:`) for *mode*, with `<agent>` spelled `_AGENT`."""
    cell = row.get("default")
    if cell is None:
        cell = row.get("value")
    arm = cell.get(mode) if isinstance(cell, dict) else cell
    return arm.replace("<agent>", _AGENT) if isinstance(arm, str) else arm


def default_reaches_anchor(entry: Any) -> bool:
    """Every per-mode arm of a per-owner row reaches its owner's identity (keyspec §0).

    One judgment, `config.reaches_identity`, read through the manifest's own arms.
    """
    from kanibako.settings.config import reaches_identity
    from kanibako.settings.keyspace_manifest import manifest_doc
    from kanibako.settings.paths import BoxMode

    rows = manifest_doc()["keys"]
    key = entry.id.replace("<agent>", _AGENT)
    for mode in BoxMode:
        def stored(name: str, mode: str = mode.value) -> Any:
            row = rows.get(name) or rows.get(name.replace(f".{_AGENT}.", ".<agent>."))
            return _mode_arm(row, mode) if isinstance(row, dict) else None

        arm = _mode_arm(entry.extra, mode.value)
        if arm is None or arm in _CONSTRUCT_TIME:
            continue
        if not reaches_identity(arm, entry.extra["owner"], mode, key=key, stored=stored):
            return False
    return True


def _keyspec_extract() -> Any:
    """`scripts/keyspec-extract.py`, the owner of the spec locator and the fence-aware
    section parser (P10). Importing it reads no credential and opens no connection."""
    import importlib.util
    import sys
    from pathlib import Path

    name = "keyspec_extract"
    if name in sys.modules:
        return sys.modules[name]
    # One literal: a bare "scripts" reads to `kinemata check` as HELPER_SCRIPTS_RELPATH.
    script = Path(__file__).resolve().parents[1] / "scripts/keyspec-extract.py"
    spec = importlib.util.spec_from_file_location(name, script)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def spec_table(section: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    """The rows of the one table in keyspace spec section *section* (`"2g"`).

    Read by section id and column, never by line number. It FAILS CLOSED, by raising:
    a missing spec file, an absent section (or one declared twice), a section holding
    no table or several, a header row other than *columns*, or a row with a
    different cell count. A raise exits the oracle nonzero, which kinemata reports
    as a failure no baseline can accept.
    """
    import re

    keyspec = _keyspec_extract()
    path = keyspec._DEFAULT_SPEC
    if not path.is_file():
        raise SystemExit(f"spec not found: {path} (set KANI_CANON to the canon root)")
    lines = path.read_text(encoding="utf-8").splitlines()
    found = keyspec.parse_sections(lines)
    if section not in found:
        raise SystemExit(f"{path}: no section {section!r}")
    # 1-based, inclusive; skips the heading line.
    body = lines[found[section].start:found[section].end]
    tables: list[list[str]] = []
    for i, line in enumerate(body):
        if line.startswith("|"):
            if i == 0 or not body[i - 1].startswith("|"):
                tables.append([])
            tables[-1].append(line)
    if len(tables) != 1:
        raise SystemExit(f"{path}: §{section} holds {len(tables)} tables, want 1")

    def cells(line: str) -> list[str]:
        parts = line.strip().removeprefix("|").split("|", len(columns) - 1)
        parts[-1] = parts[-1].removesuffix("|")
        return [part.strip() for part in parts]

    header, rule, *rows = tables[0]
    if tuple(cells(header)) != columns or not re.fullmatch(r"[|\s:-]+", rule):
        raise SystemExit(f"{path}: §{section} table header is {header!r}, want {columns}")
    parsed = [cells(line) for line in rows]
    if not parsed or any(len(row) != len(columns) for row in parsed):
        raise SystemExit(f"{path}: §{section} table has no rows or a short row")
    return [dict(zip(columns, row)) for row in parsed]


def system_settings_rows() -> list[tuple[str, str, str]]:
    """`(kind, key, default)` for every row of the keyspace spec's §2g table.

    *kind* is `value` (a backticked default), `unset` (`(unset)`: membership only,
    as the spec states no value) or `not-key` (`—`: a name the spec declares is NOT
    a key). Any other Key or Default cell shape raises.
    """
    import re

    rows = []
    for row in spec_table("2g", ("Key", "Default", "Notes")):
        key = re.fullmatch(r"`(system\.[a-z_.]+)`", row["Key"])
        value = re.fullmatch(r"`([^`]+)`", row["Default"])
        if key is None:
            raise SystemExit(f"§2g: unreadable Key cell {row['Key']!r}")
        if value is not None:
            rows.append(("value", key[1], value[1]))
        elif row["Default"] in ("(unset)", "—"):
            rows.append(("unset" if row["Default"] == "(unset)" else "not-key", key[1], ""))
        else:
            raise SystemExit(f"§2g: unreadable Default cell {row['Default']!r} for {key[1]}")
    return rows


# --------------------------------------------------------------------------- #
# §2d's FENCE notation, and the per-node cell classification.
#
# ⚑ §2d IS NOT A TABLE. Its rows are `key | default  notes` lines inside fenced
# blocks, with brace forms (`agent.shell.bindings.{ro,rw}`) -- so `spec_table`
# above, which reads §2g's ONE markdown table, reaches none of it. The row reader
# below was MOVED here from `tests/test_settings/test_manifest_spec_parity.py`,
# where it was written, so that test's §2d default-tier pin and the per-node
# descriptor views read the fence through ONE implementation (P10). That test
# imports the names it still owns from here; its SCOPE (§2d's default tier against
# the manifest) is unchanged.
#
# ⚑ THE LOCATOR AND THE HEADING PARSER ARE STILL `scripts/keyspec-extract.py`'s,
# reached through `_keyspec_extract` above. A third copy of where the spec lives is
# what the moved test's own comment warned against.
# --------------------------------------------------------------------------- #

#: The nodes the `agent.<node>.*` parity views cover: the three shipped harness
#: plugins, and the one pseudo-agent whose tier `core-defaults.yaml` declares. The
#: `default` tier is NOT here -- `test_manifest_spec_parity` owns it.
AGENT_TIER_NODES: tuple[str, ...] = ("claude", "codex", "goose", "shell")

#: The §2d fence notation for the two values YAML cannot spell the way the spec
#: writes them. `spec_notation` renders a value INTO this notation and a spec cell
#: is compared as written, so neither carrier has to change to satisfy the other.
SPEC_NULL = "<None>"
SPEC_EMPTY = "{}"

#: A row's value ends at the first run of two-or-more spaces; what follows is the
#: description column. One space is not a separator -- no declared value in §2d
#: contains one, and treating it as one would truncate any that later did.
_VALUE_END = re.compile(r"\s{2,}")

#: `bindings.{ro,rw}` -- §2d's two-arms-on-one-line notation. A way of writing two
#: rows, not the spelling of a key.
_BRACES = re.compile(r"^(?P<head>[^{}]*)\{(?P<alts>[^{}]+)\}(?P<tail>[^{}]*)$")


def expand_braces(key: str) -> list[str]:
    """The keys a spec row declares -- more than one where it uses brace notation.

    ⚑ A brace group that PARSES AS A REFERENCE is NOT enumeration notation, and
    since braced-refs step 0 the two collide: §2d writes a dest as
    `agent.claude.caches[{system.cache}/tweakcc]`, the old notation matched the
    group as a one-alternative list, and the key came back
    `…caches[system.cache/tweakcc]` -- the reference dropped and the dest left as
    a BARE RELATIVE path, the very spelling [R147] refuses. A reference is left
    exactly as written. Measured across the whole 1.8.0 spec there is no
    comma-enumeration left in any row key, so this costs nothing today; the
    notation is kept because §2d still owns the spelling.
    """
    match = _BRACES.match(key)
    if match is None:
        return [key]
    from kanibako.settings.settings_resolve import match_braced

    if match_braced("{" + match["alts"] + "}", 0) is not None:
        return [key]
    return [
        f"{match['head']}{alt.strip()}{match['tail']}"
        for alt in match["alts"].split(",")
    ]


def spec_notation(value: object) -> str:
    """A value written in the spec fence's notation."""
    if value is None:
        return SPEC_NULL
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, dict) and not value:
        return SPEC_EMPTY
    return str(value)


#: The fence's sentinel spellings, obtained by ASKING `spec_notation` for each of
#: the values YAML cannot spell as itself rather than restating its literals -- so
#: a respelled sentinel cannot drift out of sync between the two (P13). A spec row
#: whose value is the QUOTED form of one of these is a literal STRING that merely
#: reads like a sentinel, and the strip in `spec_fence_rows` must not turn it into
#: one.
_SENTINEL_VALUES: tuple[object, ...] = (None, True, False, {})
SPEC_SENTINELS = frozenset(spec_notation(value) for value in _SENTINEL_VALUES)


def spec_fence(section: str, marker: str) -> list[str]:
    """The lines INSIDE one fenced block of *section*, located by a MARKER line.

    Derived by walking headings: the section's span comes from the extraction
    script's own parser, the marker is located within that span, and the fence is
    the block that opens on the next non-blank line. It FAILS CLOSED by raising --
    a missing spec, a missing section, a marker that moved or was renamed, and an
    unclosed fence all stop here, which exits a `[[parity]]` oracle nonzero: a
    failure no baseline can accept.

    ⚑ THE MARKER IS THE BOLD NAME ALONE, matched as the START of the line. §2d's
    pseudo-agent name (`**shell**`) is a RESERVED name the spec is committed to,
    while the gloss beside it is prose, rewordable at any time.
    """
    keyspec = _keyspec_extract()
    path = keyspec._DEFAULT_SPEC
    if not path.is_file():
        raise SystemExit(f"spec not found: {path} (set KANI_CANON to the canon root)")
    lines = path.read_text(encoding="utf-8").splitlines()
    sections = keyspec.parse_sections(lines)
    if section not in sections:
        raise SystemExit(f"{path}: no section {section!r}")
    body = lines[sections[section].start - 1:sections[section].end]
    marks = [n for n, raw in enumerate(body) if raw.strip().startswith(marker)]
    if len(marks) != 1:
        raise SystemExit(
            f"{path}: §{section} carries {len(marks)} lines opening with {marker!r}, "
            f"expected exactly 1 -- the fence's opening marker moved or was renamed"
        )
    rest = body[marks[0] + 1:]
    opens = next((n for n, raw in enumerate(rest) if raw.strip()), None)
    if opens is None or not rest[opens].startswith("```"):
        raise SystemExit(f"{path}: the line after {marker!r} does not open a fenced block")
    closes = next(
        (n for n, raw in enumerate(rest[opens + 1:], start=opens + 1)
         if raw.startswith("```")),
        None,
    )
    if closes is None:
        raise SystemExit(f"{path}: §{section}'s {marker!r} fence is never closed")
    return rest[opens + 1:closes]


def spec_cell_value(cell: str) -> str:
    """One ``| value  description`` cell as the value to compare, in fence notation.

    ⚑ DOUBLE QUOTES ARE THE FENCE'S STRING DELIMITER, not part of the value, so a
    quoted sentinel keeps its quotes and compares unequal to a native one rather
    than silently passing as it. No declared value in §2d contains a quote
    character, so a stripped pair is the same string.
    """
    if len(cell) >= 2 and cell.startswith('"') and cell.endswith('"'):
        unquoted = cell[1:-1]
        if unquoted not in SPEC_SENTINELS:
            return unquoted
    return cell


def _strip_html_comments(lines: list[str]) -> list[str]:
    """*lines* with every ``<!-- ... -->`` span removed, spans crossing lines included.

    A line keeps its position (an emptied line stays, as ``""`` or its uncommented
    remainder), so nothing downstream that counts lines is shifted.
    """
    visible: list[str] = []
    in_comment = False
    for raw in lines:
        kept, rest = "", raw
        while rest:
            if in_comment:
                end = rest.find("-->")
                if end < 0:
                    rest = ""
                else:
                    rest, in_comment = rest[end + 3:], False
            else:
                start = rest.find("<!--")
                if start < 0:
                    kept, rest = kept + rest, ""
                else:
                    kept, rest, in_comment = kept + rest[:start], rest[start + 4:], True
        visible.append(kept)
    return visible


def spec_fence_rows(section: str, marker: str) -> list[tuple[str, str]]:
    """``(key, value-cell-as-written)`` for every ``key | value  notes`` row in a
    fence, brace forms expanded to one pair per key.

    ⚑ THE CELL IS RETURNED AS WRITTEN, quotes included, because whether it was
    quoted is what tells a VALUE from PROSE (`"Command Line Shell (shell)"` is a
    value; `tier REALIZATION …` is not). `spec_cell_value` and
    `classify_spec_cell` are the two readers of that distinction, and they are here
    so neither caller re-derives it.

    ⚑ AN HTML COMMENT IS NOT A ROW. §2d annotates its fences with ``<!-- -->``
    blocks, single- or multi-line, and an ``agent.<node>.x | y`` example written
    inside one is prose about a row, not a row; `_strip_html_comments` removes it
    before the row test.
    """
    rows: list[tuple[str, str]] = []
    for raw in _strip_html_comments(spec_fence(section, marker)):
        if "|" not in raw or raw.lstrip().startswith("#"):
            continue
        written_key, _, remainder = raw.partition("|")
        cell = _VALUE_END.split(remainder.strip())[0].strip()
        for key in expand_braces(written_key.strip()):
            rows.append((key, cell))
    return rows


#: Cell classes. `STATED` is the only one a VALUE is compared for; the other two
#: are named for what the spec's cell can support, as §2g's build named `(unset)`
#: membership and its NOT EXPRESSIBLE residue.
STATED = "stated"
MEMBERSHIP = "membership"
NOT_EXPRESSIBLE = "not-expressible"

#: The two cells whose value is what ABSENCE yields: no producer value is compared
#: against them. ⚑ `true` and `false` are NOT among them: the fence spells
#: a boolean `true`, and `spec_notation(True)` is `true`, so `agent.shell.allow_helpers
#: | true` and `agent.goose.env.GOOSE_DISABLE_KEYRING | true` are STATED values.
ABSENCE_CELLS = (SPEC_NULL, SPEC_EMPTY)


def classify_spec_cell(key: str, cell: str) -> str:
    """`STATED`, `MEMBERSHIP` or `NOT_EXPRESSIBLE` for one §2d ``agent.<node>.*`` cell.

    ⚑ THE THREE CLASSES ARE THE WHOLE VERDICT, STATED ONCE. `kinemata.toml` names
    the view they drive and nothing else; restating the rules beside those
    tables is the drift this function exists to prevent.

    * **NOT EXPRESSIBLE** -- the cell cannot state a default a descriptor is held
      to, and NO claim is made about it:
      - a key carrying a ``[dest]`` index: a dest-keyed category ENTRY (§2a), not
        a default -- ``bindings.ro[~/.local/bin/claude]``, ``synced[...]``;
      - a QUOTED cell, however long: it is the fence's string delimiter around a
        value (``"Command Line Shell (shell)"``);
      - an UNQUOTED cell carrying whitespace, which is every REALIZATION
        (``--model <val>``, ``tier REALIZATION ...``) and every
        ``(none in descriptor) · host_prep=...`` line;
      - a value that IS a reference -- the old ``@``-prefixed spelling or the braced
        ``{a.b}`` one (a store-relative reference; ``canon``'s arm is dynamic and
        lives in ``core_defaults.canon_default_categories``, as
        ``pseudo_tier_defaults``' own docstring records). ``{}`` is NOT one of
        these: it parses to no name and stays the ABSENCE cell below, and a
        ``{$VAR}`` cell is a variable the floor carries verbatim.
      - a value starting ``(``, or a ``<...>`` that is not ``<None>``: the
        parenthesized host source of a dest-keyed entry, or a ``<runtime-probed …>``
        placeholder.
    * **MEMBERSHIP** -- the key is named and its value is what ABSENCE yields
      (`<None>`, `{}`). A producer that yields no floor for the key (a ``None``
      or empty-dict value, which `agent_tier_defaults` drops) reads the same as
      one that omits the row, and any real value -- the STRING ``"{}"`` or
      ``"<None>"`` included -- is a finding, so a fabricated default reds and an
      honest omission does not.
    * **STATED** -- a literal the descriptor's floor must equal.

    A row this comparison does not cover at all is filtered by the caller, not
    classified here: a ``meta.`` head is a different tier.
    """
    if "[" in key or "]" in key:
        return NOT_EXPRESSIBLE
    if not cell:
        return NOT_EXPRESSIBLE
    if len(cell) >= 2 and cell.startswith('"') and cell.endswith('"'):
        return STATED
    if any(char.isspace() for char in cell):
        return NOT_EXPRESSIBLE
    if cell.startswith(("@", "(")):
        return NOT_EXPRESSIBLE
    if cell.startswith("{"):
        # A braced REFERENCE names a key whose value is delivered somewhere else, so
        # the fence cannot hold the floor to a literal -- the same reason the old
        # `@`-prefixed cell was never STATED. Two cells must NOT be swept up: `{}`
        # parses to nothing and is the ABSENCE cell, and `{$TERM}` is a VARIABLE the
        # floor carries verbatim (it is produced as exactly that string), so only
        # kind == "ref" moves here.
        from kanibako.settings.settings_resolve import match_braced

        braced = match_braced(cell, 0)
        if braced is not None and braced[0] == "ref":
            return NOT_EXPRESSIBLE
    if cell.startswith("<") and cell != SPEC_NULL:
        return NOT_EXPRESSIBLE
    return MEMBERSHIP if cell in ABSENCE_CELLS else STATED


def _node_fence_marker(node: str) -> str:
    """The line that opens *node*'s §2d fence: a `####` subheading for a plugin,
    the reserved bold pseudo-agent name for `shell`."""
    if node == "shell":
        return "**shell**"
    return f"#### `agent.{node}.*`"


def node_spec_rows(node: str) -> dict[str, tuple[str, str]]:
    """``{key: (cell-class, value-as-written)}`` for one node's §2d default rows.

    *node* is a member of :data:`AGENT_TIER_NODES`. The rows come from that node's
    OWN §2d fence, located by its heading -- read as a heading, never as a line
    number. A ``meta.agent.<node>.*`` row is a different tier and is not this
    node's default corpus.
    """
    if node not in AGENT_TIER_NODES:
        raise SystemExit(f"{node!r} is not a node with an agent.<node>.* tier")
    prefix = f"agent.{node}."
    rows: dict[str, tuple[str, str]] = {}
    for key, cell in spec_fence_rows("2d", _node_fence_marker(node)):
        if key.startswith(prefix):
            rows[key] = (classify_spec_cell(key, cell), spec_cell_value(cell))
    return dict(sorted(rows.items()))


def node_spec_defaults(node: str) -> dict[str, str]:
    """``{key: value}`` for the STATED cells -- the values a descriptor's floor is
    held to, in the fence's notation and compared as written."""
    return {
        key: value
        for key, (kind, value) in node_spec_rows(node).items()
        if kind == STATED
    }


def node_not_expressible(node: str) -> list[str]:
    """The rows this module makes NO claim about -- the list a report owes.

    ⚑ THE MEMBERSHIP CLASS IS VISIBLE ONLY THROUGH ITS ABSENCE HERE.  A
    ``<None>``/``{}`` cell names a key and states the value absence yields, so it
    is neither a value to compare (`node_spec_defaults`) nor a row to report
    (`node_not_expressible`); a producer yielding no floor for the key passes,
    and any real value is a finding.
    """
    return [
        key
        for key, (kind, _value) in node_spec_rows(node).items()
        if kind == NOT_EXPRESSIBLE
    ]


# --------------------------------------------------------------------------- #
# The DESCRIPTOR side -- what each node's own declaration PRODUCES, read through
# the production loaders and never through a second reader of the YAML.
# --------------------------------------------------------------------------- #


def _is_absent(value: object) -> bool:
    """True for a RAW producer value meaning "no floor": ``None`` or an empty dict.

    Never decided from `spec_notation`: the string ``"{}"`` is a value.
    """
    return value is None or (isinstance(value, dict) and not value)


def agent_tier_defaults(node: str) -> dict[str, str]:
    """``{agent.<node>.<leaf>: value-in-spec-notation}`` for the defaults a node's
    own declaration STATES.

    ⚑ THE PRODUCTION ROUTE, NOT A HAND-BUILT DICT. A plugin's floor is what
    ``get_target(node)().setting_descriptors()`` yields and what
    ``descriptor_floor`` turns into ``agent.<node>.<key>`` entries; its ``env:``
    rows are what ``default_envs()`` yields. The pseudo-agent's twin rows come
    from ``core_defaults.pseudo_tier_defaults()`` and ``env_default_categories()``
    -- the producers the launch itself reads.

    ⚑ NO FLOOR IS NOT A STATED DEFAULT. A producer yields ``None`` for a row with
    no default (a plugin's floorless row, the pseudo-agent's ``None`` rows), and an
    empty dict is the same absence; such a value is dropped here rather than
    produced. ⚑ THE TEST IS ON THE RAW VALUE, NEVER ITS NOTATION: a descriptor
    default that is the STRING ``"{}"`` or ``"<None>"`` renders exactly like an
    absence, and judging by notation would let that fabricated default pass. A
    plugin loader refuses a real ``null``/dict default, so a plugin cannot state an
    absence; it can only omit the default. A STATED cell is never an absence
    (`classify_spec_cell`), so the drop cannot hide one -- a producer that blanks a
    STATED row still reds as "declared, produced by nothing".

    ⚑ ``$GUEST_HOME`` IS FOLDED BACK TO ``~``. The loader expands it to the guest
    constant (§2a: the guest-home literal lives in one place), so the stored value
    is the in-box path while the spec writes the ``~`` form. The fold is the
    INVERSE of ``settings_resolve.expand_guest_home``; without it every
    ``env.KANIBAKO_DIRECTIVE_FINAL`` row would disagree with a spec that is right.
    """
    from kanibako.settings.settings_resolve import GUEST_HOME

    def notation(value: object) -> str:
        if isinstance(value, str) and value.startswith(GUEST_HOME):
            value = "~" + value[len(GUEST_HOME):]
        return spec_notation(value)

    produced: dict[str, str] = {}
    prefix = f"agent.{node}."
    for key, value in _agent_tier_floors(node).items():
        if key.startswith(prefix) and not _is_absent(value):
            produced[key] = notation(value)
    return produced


def _agent_tier_floors(node: str) -> dict[str, str | None]:
    """``{agent.<node>.<leaf>: floor-or-None}`` from the production loaders."""
    if node == "shell":
        from kanibako.settings.core_defaults import (
            env_default_categories, pseudo_tier_defaults,
        )

        rows: dict[str, str | None] = dict(pseudo_tier_defaults())
        rows.update(env_default_categories())
        return rows
    from kanibako.targets import get_target
    from kanibako.targets.base import descriptor_floor

    target = get_target(node)()
    rows = {
        f"agent.{node}.{leaf}": value
        for leaf, value in descriptor_floor(target.setting_descriptors()).items()
    }
    rows.update(target.default_envs())
    return rows


class _ViewRegistry:
    """Shared construction for the registry classes below.

    ⚑ STRUCTURAL, NOT INHERITED -- the trade `KeyspaceRegistry` makes: kinemata is
    not a dependency of this project and must not become one, so a registry class
    here satisfies `kinemata.contract.Registry` by having its members rather than
    by importing the base, and this module imports with or without kinemata
    installed.
    """

    match_mode = "strings"
    suffixes: tuple[str, ...] | None = None
    machinery: tuple[str, ...] = ()
    mentions_are_uses = True
    budget = 16 * 1024
    line_budget = 160
    boundary = ""

    def __init__(self, *, name: str, **options: object) -> None:
        self.name = name
        self.closed = False
        self.options = dict(options)
        self.rows = self._rows()

    def _rows(self) -> list[dict[str, object]]:
        raise NotImplementedError

    def entries(self) -> list[Any]:
        from kinemata.contract import Entry  # type: ignore[import-not-found]

        return [Entry(id=str(row["key"]), clauses=(), extra=row) for row in self.rows]

    def declared(self, identifier: str) -> bool:
        return identifier in {str(row["key"]) for row in self.rows}

    def resolve(self, identifier: str) -> tuple[str, ...]:
        return ()

    def detect(self, text: str) -> list[str]:
        """⚑ EMPTY, AND SAYSO. These registries answer `parity`, which hands over
        identifiers rather than source text; a literal matcher over a span of
        Python would report a mention of `agent.claude.label` in a docstring as a
        use. The project owns use-tracking for this tier, in
        `kinemata_keyspace.KeyspaceRegistry`."""
        return []

    def line(self, entry: Any) -> str:
        return str(entry.id)

    def candidates(self, text: str) -> list[str]:
        return []

    @property
    def notices(self) -> tuple[str, ...]:
        return ()


class _AgentRegistry(_ViewRegistry):
    """An ``agent.<node>.*`` registry: the base, for one node."""

    def __init__(self, *, node: str, name: str = "agent-tier", **options: object) -> None:
        if node not in AGENT_TIER_NODES:
            raise ValueError(f"registry {name!r}: {node!r} is not one of {AGENT_TIER_NODES}")
        self.node = node
        super().__init__(name=name, **options)


class AgentStatedDefaults(_AgentRegistry):
    """The defaults one node's descriptor STATES, as ``key: {default: value}``.

    The claim side of the ``agent-<node>-stated`` parity view. A row whose floor is
    an absence value (``None``, ``{}``) states no default and is not an entry, which is what makes that view
    comparable: §2d's STATED cells are literals, so a literal the descriptor does
    not state is a finding rather than a shape to reconcile.
    """

    def __init__(self, *, node: str, name: str = "agent-stated", **options: object) -> None:
        super().__init__(node=node, name=name, **options)

    def _rows(self) -> list[dict[str, object]]:
        return [
            {"key": key, "default": value}
            for key, value in agent_tier_defaults(self.node).items()
        ]


_TREE = Path(__file__).resolve().parents[1]


def core_defaults_file() -> str:
    """`core-defaults.yaml`, tree-relative."""
    from kanibako.settings.core_defaults import CORE_DEFAULTS_FILENAME

    return f"src/kanibako/data/rom/settings/{CORE_DEFAULTS_FILENAME}"


def plugin_descriptors() -> list[str]:
    """Every plugin descriptor, tree-relative.

    Read from the TREE: CI installs no plugin package, so an import would see none.
    """
    found = sorted(
        str(path.relative_to(_TREE))
        for path in _TREE.glob("packages/agent-*/src/kanibako/plugins/*/*-defaults.yaml")
    )
    if not found:
        raise ValueError(f"no plugin descriptor under {_TREE / 'packages'}")
    return found


def owner_rows(source: str) -> list[tuple[str, dict[str, Any]]]:
    """`(location, row)` for every mapping in *source* that carries `owner:`."""
    import yaml

    found: list[tuple[str, dict[str, Any]]] = []

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            if "owner" in node:
                found.append((where, node))
            for key, value in node.items():
                walk(value, f"{where}.{key}" if where else str(key))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{where}[{index}]")

    walk(yaml.safe_load((_TREE / source).read_text()), "")
    return found


def synced_cells() -> dict[str, dict[str, Any]]:
    """The manifest's `plugin_contributed` `agent.<agent>.synced` cells, by guest dest."""
    from kanibako.settings.keyspace_manifest import manifest_doc

    manifest = manifest_doc()
    return {
        dest: row
        for name, table in manifest["plugin_contributed"]["category_default_entries"].items()
        if name.endswith(".synced")
        for dest, row in table.items()
    }


class EntryOwners(_ViewRegistry):
    """The `owner:` cells the defaults files carry beside the manifest's.

    `rows` picks the view: `all` (every one, keyed `<file>:<location>`), `core`
    (`core-defaults.yaml`'s bind rows, keyed by `box_dest`) or `creds` (the
    descriptors' `cred_files` rows a synced cell names, keyed by that cell's dest).
    """

    VIEWS = ("all", "core", "creds")

    def __init__(self, *, rows: str, name: str = "entry-owners", **options: object) -> None:
        if rows not in self.VIEWS:
            raise ValueError(f"registry {name!r}: rows {rows!r} is not one of {self.VIEWS}")
        self.view = rows
        super().__init__(name=name, **options)

    def _rows(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        synced = synced_cells() if self.view == "creds" else {}
        core = core_defaults_file()
        for source in [core, *plugin_descriptors()]:
            for where, row in owner_rows(source):
                if self.view == "all":
                    key = f"{source}:{where}"
                elif self.view == "core" and source == core and "box_dest" in row:
                    key = str(row["box_dest"])
                elif (
                    self.view == "creds"
                    and where.startswith("descriptor.cred_files[")
                    and f"~/{row['home_rel']}" in synced
                ):
                    key = f"~/{row['home_rel']}"
                else:
                    continue
                out.append({"key": key, "owner": row["owner"]})
        return out
