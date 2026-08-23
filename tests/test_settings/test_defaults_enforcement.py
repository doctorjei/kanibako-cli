"""ENFORCEMENT guardrail: who may write settings through the closed write seam (D1-6).

``config_io.write_nested_key`` is the ONE primitive that puts a value into a
settings FILE.  "Defaults live in defaults files" (T1) is therefore enforceable
at exactly one syntactic point: the set of call sites of that seam.  A default
that is written at runtime is a default that does not live in a defaults file —
it lives in whatever code happened to run — so any NEW caller of this seam is
either a sanctioned write surface (``config set``, ``setup``, box registration)
or a defaults leak, and the two must not be told apart by reading a diff.

This module scans ``src/kanibako/`` and every plugin's ``packages/*/src/kanibako/``
for CALLS to the seam and asserts the caller set is EXACTLY the named allowlist
below.  Adding a call site anywhere else goes RED naming the file.

Scope note: the guard covers the SHIPPED source trees only.  Tests call the seam
freely (they are building fixtures, not shipping behavior), the DEFINITION site
is excluded by construction (a ``def`` is not a ``Call``), and so are ``from
… import write_nested_key`` lines (an ``ImportFrom`` is not a ``Call`` either) —
the scan is an AST walk, not a grep, so neither needs an allowlist exemption.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/``
(house style is 2, but the file it pairs with — ``test_defaults_golden.py`` —
and the rest of this directory are 4).
"""

from __future__ import annotations

import ast
from functools import cache
from pathlib import Path

from tests.support.repo import REPO_ROOT

#: The seam.  A fixed syntactic token: the guard is only meaningful while this
#: name IS the single file-write primitive.
_SEAM = "write_nested_key"

#: Where the seam is defined.  Scanned like any other file — it must show up with
#: ZERO call sites, which is what proves the scan is running at all (see
#: :meth:`TestSeamScan.test_the_scan_reaches_the_definition_site_and_counts_no_call`).
_DEFINITION_SITE = "src/kanibako/settings/config_io.py"

#: The SANCTIONED write surfaces, keyed by repo-relative path → expected number of
#: call sites in that file.
#:
#: Counts, not just paths, and not line numbers: line numbers drift on any edit
#: above them and would make this a nuisance, but a bare path SET would silently
#: allow a brand-new defaults write dropped into an already-sanctioned file —
#: which is the same violation the guard exists to catch.  A count changes only
#: when someone adds or removes a write, and that is exactly the review moment.
_SANCTIONED: dict[str, int] = {
    # ``kanibako config set`` and friends — the user-intent write path.  Seven
    # sites: the per-scope dispatch arms plus the system-scope leaf write.
    # ⚑ 6 → 7 on 2026-08-23, and the JUSTIFICATION the guard asks for: the new site
    # is the setup MARKER's own ``set`` arm (`SETUP_MARKER_KEY`), which writes
    # ``system.setup_completed`` into the BOOTSTRAP config file's ``system:`` table.
    # It records USER INTENT in the strictest sense — it runs only when a user typed
    # ``system set system.setup_completed=…`` — and it writes no default: the key has
    # none (spec §2g: unset means setup has never run). It is a new ARM on an already
    # sanctioned surface, not a new surface, which is exactly the case this count
    # exists to surface for review rather than to forbid.
    "src/kanibako/settings/config_interface.py": 7,
    # The agent settings file writer (``agents/<node>/agent.yaml``), which by
    # the FILE-PURITY invariant may only ever carry user-intent values.
    "src/kanibako/settings/agent_file.py": 1,
    # Standalone box registration stamps the generated ``workset.kuid``.
    "src/kanibako/settings/paths.py": 1,
    # ``kanibako setup`` records the chosen agent as system-scope user intent.
    "src/kanibako/commands/setup_cmd.py": 1,
}

#: QUARANTINED EXCEPTIONS — **EMPTY, and that is the point.**
#:
#: It held exactly one: the COLORTERM first-run write in ``cli.py``, a WRITTEN
#: VALUE standing in for a default, i.e. precisely the thing this guard forbids.
#: MBR-2's D1-4 deleted that call site and this entry with it (the removal was
#: self-enforcing — the stale-entry test below reds on an allowlist file with zero
#: call sites), so the allowlist is exception-free and the guard is exact.
#:
#: Do NOT add entries here.  A new entry means a defaults write outside the
#: defaults system; the cure is to move the value into a defaults file, not to
#: re-open this table.
_QUARANTINED: dict[str, int] = {}

#: Everything permitted to call the seam.
_ALLOWLIST: dict[str, int] = {**_SANCTIONED, **_QUARANTINED}


def _scan_roots() -> list[Path]:
    """The shipped source trees: the core package plus each plugin's package."""
    roots = [REPO_ROOT / "src" / "kanibako"]
    roots.extend(sorted((REPO_ROOT / "packages").glob("*/src/kanibako")))
    return roots


@cache
def _call_sites() -> dict[str, list[int]]:
    """Repo-relative path → sorted line numbers of every CALL to the seam.

    An AST walk rather than a regex: it counts calls only, so the ``def`` and the
    ``from … import`` lines are excluded by construction, and a mention inside a
    docstring or comment cannot produce a phantom hit.
    """
    found: dict[str, list[int]] = {}
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
                if name == _SEAM:
                    rel = py.relative_to(REPO_ROOT).as_posix()
                    found.setdefault(rel, []).append(node.lineno)
    return {rel: sorted(lines) for rel, lines in found.items()}


def _cite(rel: str) -> str:
    """``path:line,line`` for an error message."""
    return f"{rel}:{','.join(str(n) for n in _call_sites().get(rel, []))}"


class TestSeamScan:
    """The scan's own preconditions — a guard that scans nothing reports clean."""

    def test_every_scan_root_exists(self):
        """A moved/renamed source tree must fail LOUDLY, not scan zero files."""
        missing = [str(r) for r in _scan_roots() if not r.is_dir()]
        assert not missing, f"scan root(s) gone — fix the paths in _scan_roots(): {missing}"
        assert len(_scan_roots()) >= 2, (
            f"expected the core tree plus at least one plugin tree, got {_scan_roots()}"
        )

    def test_the_scan_reaches_the_definition_site_and_counts_no_call(self):
        """``config_io.py`` is scanned, defines the seam, and CALLS it zero times.

        The anti-vacuity check: it proves the walk actually reads files (the seam
        name is in this one) while confirming that a ``def`` is not counted as a
        call.  Without it, a broken scan returning ``{}`` would pass every
        assertion below.
        """
        definition = REPO_ROOT / _DEFINITION_SITE
        assert definition.is_file(), f"seam definition not found at {definition}"
        assert f"def {_SEAM}(" in definition.read_text(), (
            f"{_DEFINITION_SITE} no longer defines {_SEAM} — the seam moved; "
            f"re-point _DEFINITION_SITE and re-derive the allowlist"
        )
        assert _DEFINITION_SITE not in _call_sites(), (
            f"the definition site must contribute no CALL sites, found "
            f"{_cite(_DEFINITION_SITE)}"
        )
        assert _call_sites(), (
            "the scan found no call sites at all — the walk is broken or the seam "
            "was renamed; it is not credible that shipped code never writes settings"
        )


class TestWriteSeamCallers:
    """The caller set of the settings write seam is EXACTLY the allowlist."""

    def test_no_write_nested_key_caller_outside_the_allowlist(self):
        """A new caller is a defaults write outside the defaults system."""
        unexpected = sorted(set(_call_sites()) - set(_ALLOWLIST))
        assert not unexpected, (
            f"{_SEAM} called outside the sanctioned write surfaces:\n  "
            + "\n  ".join(_cite(rel) for rel in unexpected)
            + f"\n\nDefaults live in defaults files ({_DEFINITION_SITE} is the closed "
            f"write seam). If this really is a new user-intent write surface, add it to "
            f"_SANCTIONED with a comment saying whose intent it records — do not add it "
            f"to _QUARANTINED."
        )

    def test_every_allowlist_entry_still_has_a_call_site(self):
        """No STALE entry: an allowlisted file with zero calls fails.

        This is what made the ``cli.py`` quarantine self-verifying — when D1-4
        deleted the COLORTERM write it red until the entry went too.  It keeps
        earning its place on the SANCTIONED rows: a write surface that moves out
        of its file leaves an entry that permits a future write nobody reviewed.
        """
        stale = sorted(rel for rel in _ALLOWLIST if rel not in _call_sites())
        assert not stale, (
            f"allowlist entries with NO {_SEAM} call site (the write moved or was "
            f"deleted — remove the entry):\n  " + "\n  ".join(stale)
        )

    def test_call_site_counts_match_the_allowlist(self):
        """Per-file counts pin a write ADDED to an already-sanctioned file."""
        drifted = {
            rel: (expected, len(_call_sites()[rel]))
            for rel, expected in _ALLOWLIST.items()
            if rel in _call_sites() and len(_call_sites()[rel]) != expected
        }
        assert not drifted, (
            f"{_SEAM} call-site COUNT changed (expected, actual):\n  "
            + "\n  ".join(
                f"{_cite(rel)} — expected {exp}, found {act}"
                for rel, (exp, act) in sorted(drifted.items())
            )
            + "\n\nA count that GREW is a new write: justify it as user intent before "
            "updating the number."
        )
