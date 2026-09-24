"""Predicates `kinemata.toml` names in a `[[registry]] where`, to narrow a view.

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


def standalone_is_absent(entry: Any) -> bool:
    """The row's `default:` map declares its `standalone` arm as NOTHING.

    Two spellings, because the manifest uses both: a YAML `null`, and a `<…>`
    prose placeholder (`workset.kuid` reads `<generated at creation>`).

    ⚑ THE `standalone` KEY MUST BE PRESENT, and that is the half a declarative
    guard cannot express. `at_path` returns MISSING for a row with no `default:`
    map at all, `_value` renders MISSING as `None`, and a `matches` guard would
    therefore sweep in every row that never had an arm to declare. Two questions
    -- *is there an arm* and *is it empty* -- and a `where` guard claims one.
    """
    arm = entry.extra.get("default")
    if not isinstance(arm, dict) or "standalone" not in arm:
        return False
    value = arm["standalone"]
    return value is None or (
        isinstance(value, str) and value.startswith("<") and value.endswith(">")
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
