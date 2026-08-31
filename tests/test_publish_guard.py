"""Every path that uploads to PyPI runs the collision guard, and runs it FIRST.

THE RULE, NOT AN INVENTORY.  The corpus is DERIVED from the mechanisms an upload
takes here -- the ``pypa/gh-action-pypi-publish`` action, and ``twine upload`` in
a workflow ``run:`` step or a line of a repo script -- never from a hand-written
list of job names or filenames, so a NEW publishing path that forgets
``scripts/check-publish-collisions.py`` fails here on the day it lands, which is
the actual failure mode.  Membership is decided by a DIFFERENT property than the
one asserted (a path is in the corpus because it uploads; it is asserted to guard
first), so a path can never leave the corpus by breaking the very thing under
test.

TWO CARRIERS, ONE RULE.  The upload paths do not all live in ``release.yml``.
``scripts/build-all.sh --upload`` ships to the same index by hand, and it was
unguarded for exactly as long as this test read the workflow alone -- the
invariant had four members and the test could see three.  A workflow step and a
script line are both "an ordered unit that may upload and may guard", so both
carriers reduce to the same shape and one ordering rule covers them.

WHY THIS IS A STATIC TEST AND NOT AN EXERCISED ONE.  A release-lane guard is
normally proven by a dispatch that is expected to REFUSE, before it is trusted on
one expected to pass.  ``promote`` cannot be proven that way: its ``if:`` requires
``github.event_name == 'push'``, so a ``workflow_dispatch`` can never reach it and
the only way to run the job at all is to push a production tag at prod PyPI.
Reading the workflow is the only proof mechanism that job admits.

WHAT THIS STILL DOES NOT COVER.

* A job that uploads by CALLING a script, rather than uploading in its own step,
  is credited to the SCRIPT and not to the job -- the workflow corpus reads a
  step's own ``uses:`` and ``run:``, never what the command it invokes goes on to
  do.  That is sound only while every such script is itself in the corpus, which
  is why the script corpus is GLOBBED rather than listed.
* Conditions are not read on either carrier.  A workflow step ``if:``, or a shell
  ``if`` that skips the guard at run time, still passes here.
* Only a WHOLE-LINE comment is discounted on the guard side.  A guard name
  trailing an executing line, or one sitting inside a here-doc or a quoted
  string, still reads as an invocation.
* Ordering is POSITIONAL -- step index, or line number.  That is execution order
  for a linear script, and a lie for a guard buried in a function defined above
  the upload that calls it.
* An upload reaching PyPI by a third mechanism -- a raw request to the upload
  API, or another publishing front end -- is invisible to both carriers.
* Only ``scripts/`` is globbed.  An upload script living elsewhere -- a
  workflow-local helper, a make target -- is outside the corpus.  That directory
  is this repo's declared home for scripts; globbing the whole tree instead would
  sweep in every file that merely NAMES the mechanism, this one included.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support.repo import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
SCRIPTS = REPO_ROOT / "scripts"

#: The two mechanisms a PyPI upload takes from this repo.  Both, because
#: ``twine`` is already installed and used in the workflow and
#: ``scripts/build-all.sh`` already uploads with it -- so a ``run:`` upload is a
#: shape this repo reaches for, and matching only the action would leave such a
#: path out of the corpus in SILENCE rather than failing it.  Substring, not a
#: parse: the near miss the workflow actually runs, ``twine check dist/*``, does
#: not contain it -- and a match looser than needed reds loudly on a path that
#: turns out not to upload, where a tighter one passes quietly on a path that
#: does.  A prose mention in a comment is therefore a FALSE POSITIVE by design.
PUBLISH_ACTION = "pypa/gh-action-pypi-publish"
TWINE_UPLOAD = "twine upload"

#: The guard that refuses a version PyPI already serves with different content.
GUARD_SCRIPT = "scripts/check-publish-collisions.py"


def _jobs(workflow: Path) -> dict[str, Any]:
  """The workflow's ``jobs:`` table; empty when the parse finds nothing."""
  parsed = yaml.safe_load(workflow.read_text(encoding="utf-8")) or {}
  return parsed.get("jobs") or {}


def _steps(job: Any) -> list[dict[str, Any]]:
  return [step for step in (job or {}).get("steps") or [] if isinstance(step, dict)]


def _lines_as_steps(script: Path) -> list[dict[str, Any]]:
  """A script's lines as one-``run:``-each units, so both carriers share one rule."""
  text = script.read_text(encoding="utf-8", errors="replace")
  return [{"run": line} for line in text.splitlines()]


def _uploads_to_pypi(unit: dict[str, Any]) -> bool:
  return str(unit.get("uses", "")).startswith(PUBLISH_ACTION) or TWINE_UPLOAD in str(
    unit.get("run", "")
  )


def _runs_guard(unit: dict[str, Any]) -> bool:
  """Whether *unit* INVOKES the guard; a commented-out line is not an invocation.

  Tight where ``_uploads_to_pypi`` is loose, and for the same reason that one is
  loose: over-crediting a guard passes QUIETLY over an upload that has none --
  exactly the residue a release debug leaves behind when it comments the guard out
  and the name survives in the comment.  Dropping whole-line comments covers both
  carriers at once: a script line is a one-line ``run:``, a workflow step's is a
  block.
  """
  executed = "\n".join(
    line for line in str(unit.get("run", "")).splitlines() if not line.lstrip().startswith("#")
  )
  return GUARD_SCRIPT in executed


def publish_jobs(workflow: Path) -> dict[str, list[dict[str, Any]]]:
  """Label -> its steps, for every job in *workflow* that uploads to PyPI."""
  return {
    f"{workflow.name} job '{name}'": _steps(job)
    for name, job in _jobs(workflow).items()
    if any(_uploads_to_pypi(step) for step in _steps(job))
  }


def publish_scripts(scripts: Path) -> dict[str, list[dict[str, Any]]]:
  """Label -> its lines, for every file in *scripts* that uploads to PyPI.

  Globs the directory rather than naming files: a second upload script must join
  the corpus by existing, not by someone remembering to add it here.  Labels off
  *scripts* like ``publish_jobs`` labels off *workflow*, so any directory works
  and the signature is the whole input.
  """
  found: dict[str, list[dict[str, Any]]] = {}
  for path in sorted(p for p in scripts.iterdir() if p.is_file()):
    units = _lines_as_steps(path)
    if any(_uploads_to_pypi(unit) for unit in units):
      found[f"{scripts.name}/{path.name}"] = units
  return found


#: Discovered once.  ``test_the_workflow_still_publishes`` is what makes an empty
#: one RED -- a parametrized test over an empty list collects nothing and reports
#: green, which would manufacture confidence instead of supplying it.
_UPLOAD_PATHS = {**publish_jobs(WORKFLOW), **publish_scripts(SCRIPTS)}


def test_the_workflow_still_publishes() -> None:
  """A workflow with no discoverable upload job is a broken test, not a clean one.

  Reds on a renamed workflow file, an upload moved off BOTH known mechanisms, and
  any parse that yields no jobs -- each of which would otherwise empty the corpus
  and leave the assertion below covering nothing.

  The emptiness check is on the WORKFLOW half alone, deliberately.  ``release.yml``
  publishing is not optional, so zero jobs there is always a defect; zero
  uploading SCRIPTS is a legitimate state -- deleting the manual route would be a
  fix, not a regression -- so asserting on the union would red on it.
  """
  assert publish_jobs(WORKFLOW), (
    f"no job in {WORKFLOW} uploads to PyPI — neither {PUBLISH_ACTION} nor a "
    f"`{TWINE_UPLOAD}` run step appears in any job, so either the workflow no "
    f"longer publishes, the upload moved to a third mechanism _uploads_to_pypi "
    f"cannot see, or this test is reading the wrong file; either way the guard "
    f"assertion below now covers less than it claims"
  )


@pytest.mark.parametrize("path", sorted(_UPLOAD_PATHS))
def test_guard_runs_before_the_upload(path: str) -> None:
  """The collision guard must run, and must run BEFORE the path's first upload.

  Presence alone is not the rule: ``skip-existing: true`` drops a colliding
  upload in silence, so a guard placed after the upload reports a problem that
  has already shipped.
  """
  units = _UPLOAD_PATHS[path]
  guards = [i for i, unit in enumerate(units) if _runs_guard(unit)]
  uploads = [i for i, unit in enumerate(units) if _uploads_to_pypi(unit)]
  assert guards, (
    f"{path} uploads to PyPI but never runs {GUARD_SCRIPT} — an upload with "
    f"`skip-existing` silently drops a colliding version and ships the OLD files"
  )
  assert min(guards) < min(uploads), (
    f"{path} runs {GUARD_SCRIPT} at position {min(guards)}, after its first "
    f"upload at position {min(uploads)} — a guard behind the upload is decorative"
  )
