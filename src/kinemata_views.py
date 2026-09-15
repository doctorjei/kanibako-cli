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
