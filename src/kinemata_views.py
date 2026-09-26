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
#: is listed because the realistic future row is that row's exact analogue --
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
    """
    from pathlib import Path
    from types import SimpleNamespace

    return SimpleNamespace(
        primary_workset=_root_or_decoy(mode, "primary_workset"),
        channels_common=Path("@system.channels.common"),
        channels_chat=Path("@system.channels.chat"),
        channels_mailboxes=Path("@system.channels.mailboxes"),
        channels_share=Path("@system.channels.share"),
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
