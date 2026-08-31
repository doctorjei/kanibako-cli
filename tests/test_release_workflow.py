"""Every ``release.yml`` job that uploads to PyPI runs the collision guard first.

THE RULE, NOT AN INVENTORY.  The job corpus is DERIVED from the two mechanisms an
upload takes here -- the ``pypa/gh-action-pypi-publish`` action, and a ``twine
upload`` in a ``run:`` step -- never from a hand-written list of job names, so a
NEW publishing job that forgets ``scripts/check-publish-collisions.py`` fails
here on the day it lands, which is the actual failure mode.  Membership is
decided by a DIFFERENT property than the one asserted (a job is in the corpus
because it uploads; it is asserted to guard first), so a job can never leave the
corpus by breaking the very thing under test.

WHY THIS IS A STATIC TEST AND NOT AN EXERCISED ONE.  A release-lane guard is
normally proven by a dispatch that is expected to REFUSE, before it is trusted on
one expected to pass.  ``promote`` cannot be proven that way: its ``if:`` requires
``github.event_name == 'push'``, so a ``workflow_dispatch`` can never reach it and
the only way to run the job at all is to push a production tag at prod PyPI.
Reading the workflow is the only proof mechanism that job admits.

WHAT THIS DOES NOT COVER.  The real invariant is that EVERY path which uploads to
PyPI refuses a content collision first, and this test sees only the paths inside
``release.yml``.  ``scripts/build-all.sh --upload`` is a fourth member of that
set and is invisible here: it ends in ``twine upload --skip-existing`` with no
collision check at all.  A job that uploaded by CALLING a script like that, rather
than by uploading in its own step, would be invisible for the same reason -- the
corpus reads a step's own ``uses:`` and ``run:``, never what the command it invokes
goes on to do.  Nor does this test read step ``if:`` conditions -- a guard gated
more narrowly than the upload it protects would still pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.support.repo import REPO_ROOT

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"

#: The two mechanisms a PyPI upload takes from this workflow.  Both, because
#: ``twine`` is already installed and used here and ``scripts/build-all.sh``
#: already uploads with it -- so a ``run:`` upload is a shape this repo reaches
#: for, and matching only the action would leave such a job out of the corpus in
#: SILENCE rather than failing it.  Substring, not a parse: the near miss this
#: workflow actually runs, ``twine check dist/*``, does not contain it -- and a
#: match looser than needed reds loudly on a job that turns out not to upload,
#: where a tighter one passes quietly on a job that does.
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


def _uploads_to_pypi(step: dict[str, Any]) -> bool:
  return str(step.get("uses", "")).startswith(PUBLISH_ACTION) or TWINE_UPLOAD in str(
    step.get("run", "")
  )


def _runs_guard(step: dict[str, Any]) -> bool:
  return GUARD_SCRIPT in str(step.get("run", ""))


def publish_jobs(workflow: Path) -> dict[str, list[dict[str, Any]]]:
  """Job name -> its steps, for every job in *workflow* that uploads to PyPI."""
  return {
    name: _steps(job)
    for name, job in _jobs(workflow).items()
    if any(_uploads_to_pypi(step) for step in _steps(job))
  }


#: Discovered once.  ``test_publish_jobs_are_discovered`` is what makes an empty
#: one RED -- a parametrized test over an empty list collects nothing and reports
#: green, which would manufacture confidence instead of supplying it.
_PUBLISH_JOBS = publish_jobs(WORKFLOW)


def test_publish_jobs_are_discovered() -> None:
  """A workflow with no discoverable upload job is a broken test, not a clean one.

  Reds on a renamed workflow file, an upload moved off BOTH known mechanisms, and
  any parse that yields no jobs -- each of which would otherwise empty the corpus
  and leave every assertion below covering nothing.
  """
  assert _PUBLISH_JOBS, (
    f"no job in {WORKFLOW} uploads to PyPI — neither {PUBLISH_ACTION} nor a "
    f"`{TWINE_UPLOAD}` run step appears in any job, so either the workflow no "
    f"longer publishes, the upload moved to a third mechanism _uploads_to_pypi "
    f"cannot see, or this test is reading the wrong file; either way the guard "
    f"assertions below now cover nothing"
  )


@pytest.mark.parametrize("job", sorted(_PUBLISH_JOBS))
def test_guard_runs_before_the_upload(job: str) -> None:
  """The collision guard must run, and must run BEFORE the job's first upload.

  Presence alone is not the rule: ``skip-existing: true`` drops a colliding
  upload in silence, so a guard placed after the upload reports a problem that
  has already shipped.
  """
  steps = _PUBLISH_JOBS[job]
  guards = [i for i, step in enumerate(steps) if _runs_guard(step)]
  uploads = [i for i, step in enumerate(steps) if _uploads_to_pypi(step)]
  assert guards, (
    f"job '{job}' uploads to PyPI but never runs {GUARD_SCRIPT} — an upload with "
    f"`skip-existing` silently drops a colliding version and ships the OLD files"
  )
  assert min(guards) < min(uploads), (
    f"job '{job}' runs {GUARD_SCRIPT} at step {min(guards)}, after its first "
    f"upload at step {min(uploads)} — a guard behind the upload is decorative"
  )
