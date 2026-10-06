"""ENFORCEMENT guardrail: who may write a settings file through ``config_io`` (D1-6).

"Defaults live in defaults files" (T1): a value written at runtime lives in whatever code
ran, not in a defaults file.  ``settings/config_io.py`` holds SIX file writers —
``write_nested_key``, ``remove_nested_key``, ``write_root_key``, ``remove_root_key``,
``dump_doc``, and ``dump_doc_commented`` — and the four mutators compose ``dump_doc``.
Every ``config_io`` write to disk is therefore a CALL to one of the six, so the guard is
the set of those call sites, keyed per (file, writer) and counted.

Each shipped call site is exactly one of:

- a SANCTIONED settings write: user intent (``config set``/``unset``/``reset``,
  ``agent`` writes and resets, ``setup``, ``workset share``, the creation flags a user
  typed), a lifecycle CARRY of a box tier the user already authored (move, duplicate), or
  IDENTITY written at create (the standalone root ``workset.yaml``, below);
- a QUARANTINED settings write: a real defaults leak, allowlisted only so it stays named;
- a write in a NON-SETTINGS file (journal, registries, a delivery document), exempt per
  file with its reason.

Anything else goes RED naming ``path:line``.  A ``def`` is not a ``Call`` and neither is
an ``ImportFrom``, so the scan (an AST walk over the shipped trees, tests excluded) needs
no exemption for either.  NOT SEEN: callers of a WRAPPER that itself calls a writer (e.g.
``config.write_box_enable_vault``) and writes that bypass ``config_io`` altogether.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/``.
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

from tests.support.repo import REPO_ROOT

#: The writers.  Fixed syntactic tokens: every ``config_io`` file write is a call to one.
_WRITERS = (
    "write_nested_key", "remove_nested_key", "write_root_key", "remove_root_key",
    "dump_doc", "dump_doc_commented",
)

#: Where the writers are defined.  Scanned like any other file; its only calls are the
#: composition below, which is what proves the scan reads files at all.
_DEFINITION_SITE = "src/kanibako/settings/config_io.py"

#: The definition site's own composition: the four mutators each end in ``dump_doc``.
_COMPOSITION: dict[tuple[str, str], int] = {(_DEFINITION_SITE, "dump_doc"): 4}

_CONFIG_INTERFACE = "src/kanibako/settings/config_interface.py"
_AGENT_FILE = "src/kanibako/settings/agent_file.py"
_CONFIG = "src/kanibako/settings/config.py"

#: SANCTIONED settings writes, (repo-relative path, writer) → call-site count.  Counts, not
#: line numbers: a count changes only when a write is added or removed — the review moment.
_SANCTIONED: dict[tuple[str, str], int] = {
    # ``config set``/``unset``/``reset`` at every scope — user intent.  The eighth
    # ``write_nested_key`` arm is ``system set system.setup_completed`` (no default, §2g);
    # ``agent.default`` env/secret_path ``set`` arms ride the same dispatch (§0, §2a).
    (_CONFIG_INTERFACE, "write_nested_key"): 8,
    (_CONFIG_INTERFACE, "write_root_key"): 1,
    (_CONFIG_INTERFACE, "remove_nested_key"): 8,
    (_CONFIG_INTERFACE, "remove_root_key"): 1,
    (_CONFIG_INTERFACE, "dump_doc"): 1,  # ``reset --all`` dropping whole scope tables
    # ``agents/<node>/agent.yaml`` — user intent only (FILE-PURITY): leaf write/remove, ``save``
    # of a generated config every Target returns EMPTY, and ``agent reset --all``.
    (_AGENT_FILE, "write_nested_key"): 1,
    (_AGENT_FILE, "remove_nested_key"): 1,
    (_AGENT_FILE, "dump_doc"): 2,
    # ``kanibako setup`` records the chosen agent as system-scope user intent.
    ("src/kanibako/commands/setup_cmd.py", "write_nested_key"): 1,
    # ``persist_creation_flags`` (the §1A create exception), ``write_box_enable_vault``
    # (sparse ``false``), and ``unset_project_config_key`` (reset).
    (_CONFIG, "dump_doc"): 5,
    # ``workset create --image/--no-vault`` and ``workset share add``/``remove``.
    ("src/kanibako/commands/workset_cmd.py", "dump_doc"): 3,
    # CARRY: ``box duplicate`` / ``box move`` rewrite the box tier the user authored, minus
    # any ``workset:`` section (``config.carried_box_settings``).
    ("src/kanibako/commands/box/_duplicate.py", "dump_doc"): 2,
    ("src/kanibako/commands/box/_lifecycle.py", "dump_doc"): 1,
    # IDENTITY, not a default: standalone create writes ``workset.kuid`` (GENERATED at
    # creation) and ``workset.registry: null``, whose stored presence DEFINES standalone
    # (keyspec §2c, both rows; mode is identity, not behavior).  Do not "fix" it.
    ("src/kanibako/settings/paths.py", "dump_doc_commented"): 1,
}

#: QUARANTINED settings writes — real defaults leaks, named here until src is fixed.  Do
#: NOT add entries: the cure is to move the value into a defaults file.
_QUARANTINED: dict[tuple[str, str], int] = {}

#: Files whose writer calls write NO settings file, path → reason.  Per file, any writer.
_NON_SETTINGS: dict[str, str] = {
    "src/kanibako/launch/journal.py": "the lifecycle journal (config.journal)",
    "src/kanibako/project/registry_store.py": "the name registry (config.registry)",
    "src/kanibako/project/workset_registry.py": "a workset's registry.yaml",
    "src/kanibako/channels/helpers.py": "a helper's spawn.yaml delivery doc, no cascade tier",
}

#: Everything permitted, summed: one (file, writer) may hold sanctioned AND quarantined sites.
_ALLOWLIST: dict[tuple[str, str], int] = {
    key: _SANCTIONED.get(key, 0) + _QUARANTINED.get(key, 0)
    for key in {*_SANCTIONED, *_QUARANTINED}
}


def _scan_roots() -> list[Path]:
    """The shipped source trees: the core package plus each plugin's package."""
    roots = [REPO_ROOT / "src" / "kanibako"]
    roots.extend(sorted((REPO_ROOT / "packages").glob("*/src/kanibako")))
    return roots


@cache
def _call_sites() -> dict[tuple[str, str], list[int]]:
    """(repo-relative path, writer) → sorted line numbers of every CALL to that writer."""
    found: dict[tuple[str, str], list[int]] = {}
    for root in _scan_roots():
        for py in sorted(root.rglob("*.py")):
            tree = ast.parse(py.read_text(), filename=str(py))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = (
                    func.id if isinstance(func, ast.Name)
                    else func.attr if isinstance(func, ast.Attribute)
                    else None
                )
                if name in _WRITERS:
                    rel = py.relative_to(REPO_ROOT).as_posix()
                    found.setdefault((rel, name), []).append(node.lineno)
    return {key: sorted(lines) for key, lines in found.items()}


def _files_with_calls() -> set[str]:
    return {rel for rel, _ in _call_sites()}


def _cite(key: tuple[str, str]) -> str:
    """``path:line,line (writer)`` for an error message."""
    rel, writer = key
    return f"{rel}:{','.join(str(n) for n in _call_sites().get(key, []))} ({writer})"


class TestSeamScan:
    """The scan's own preconditions — a guard that scans nothing reports clean."""

    def test_every_scan_root_exists(self):
        """A moved/renamed source tree must fail LOUDLY, not scan zero files."""
        missing = [str(r) for r in _scan_roots() if not r.is_dir()]
        assert not missing, f"scan root(s) gone — fix the paths in _scan_roots(): {missing}"
        assert len(_scan_roots()) >= 2, (
            f"expected the core tree plus at least one plugin tree, got {_scan_roots()}"
        )

    def test_the_scan_reaches_the_definition_site_and_counts_only_its_composition(self):
        """``config_io.py`` defines every writer and calls them only as ``_COMPOSITION`` says.

        The anti-vacuity check: a broken scan returning ``{}`` would pass every test below.
        """
        definition = REPO_ROOT / _DEFINITION_SITE
        assert definition.is_file(), f"writer definitions not found at {definition}"
        text = definition.read_text()
        undefined = [w for w in _WRITERS if f"def {w}(" not in text]
        assert not undefined, (
            f"{_DEFINITION_SITE} no longer defines {undefined} — a writer moved; re-point "
            f"_DEFINITION_SITE and re-derive the allowlist"
        )
        own = {
            key: len(lines) for key, lines in _call_sites().items()
            if key[0] == _DEFINITION_SITE
        }
        assert own == _COMPOSITION, (
            f"{_DEFINITION_SITE}'s own writer calls changed: expected {_COMPOSITION}, "
            f"found {own}"
        )
        assert set(_call_sites()) - set(_COMPOSITION), (
            "the scan found no call site outside the definition — the walk is broken"
        )

    def test_the_lists_do_not_overlap(self):
        """A file is settings or non-settings, never both; the definition site is neither."""
        allowlisted = {rel for rel, _ in _ALLOWLIST}
        both = sorted(allowlisted & set(_NON_SETTINGS))
        assert not both, f"in both the allowlist and _NON_SETTINGS: {both}"
        assert _DEFINITION_SITE not in allowlisted | set(_NON_SETTINGS)


class TestWriteSeamCallers:
    """The writer call set is EXACTLY the allowlist plus the non-settings files."""

    def test_no_writer_call_outside_the_allowlist(self):
        """A new settings write is a defaults write outside the defaults system."""
        unexpected = sorted(
            key for key in _call_sites()
            if key not in _ALLOWLIST and key not in _COMPOSITION
            and key[0] not in _NON_SETTINGS
        )
        assert not unexpected, (
            "config_io writer called outside the sanctioned write surfaces:\n  "
            + "\n  ".join(_cite(key) for key in unexpected)
            + "\n\nDefaults live in defaults files. A new user-intent surface goes in "
            "_SANCTIONED with whose intent it records; a file that writes no settings "
            "file goes in _NON_SETTINGS with its reason — never _QUARANTINED."
        )

    def test_every_allowlist_entry_still_has_a_call_site(self):
        """No STALE entry in either list: a write that moved leaves a permit nobody reviewed."""
        stale = sorted(
            [_cite(key) for key in _ALLOWLIST if key not in _call_sites()]
            + [rel for rel in _NON_SETTINGS if rel not in _files_with_calls()]
        )
        assert not stale, (
            "allowlist entries with NO writer call site (the write moved or was "
            "deleted — remove the entry):\n  " + "\n  ".join(stale)
        )

    def test_call_site_counts_match_the_allowlist(self):
        """Per-(file, writer) counts pin a write ADDED to an already-sanctioned file."""
        drifted = {
            key: (expected, len(_call_sites()[key]))
            for key, expected in _ALLOWLIST.items()
            if key in _call_sites() and len(_call_sites()[key]) != expected
        }
        assert not drifted, (
            "config_io writer call-site COUNT changed (expected, actual):\n  "
            + "\n  ".join(
                f"{_cite(key)} — expected {exp}, found {act}"
                for key, (exp, act) in sorted(drifted.items())
            )
            + "\n\nA count that GREW is a new write: justify it as user intent before "
            "updating the number."
        )
