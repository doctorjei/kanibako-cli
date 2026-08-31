#!/usr/bin/env python3
"""Refuse to publish a version whose content differs from what PyPI already has.

THE FAILURE THIS EXISTS FOR (measured 2026-08-17).  ``kanibako-agent-goose`` and
``kanibako-agent-codex`` version INDEPENDENTLY of the cli/agent-claude/meta train,
so the release job builds them at their static ``pyproject.toml`` version.  Their
descriptor YAMLs took breaking changes -- ``safe_bypass:`` -> ``access_realization:``,
``container_env:`` -> the top-level ``env:`` section -- with no version bump.  The
job rebuilt them at ``0.3.0``, the upload ran with ``skip-existing: true``, PyPI
already had ``0.3.0``, and both uploads were SILENTLY SKIPPED.  The fixed files
were built and then not shipped, and nothing anywhere said so.  What reached a
user installing the meta package was a wheel the current cli refuses to load --
and because plugin discovery was unguarded, that bricked every command including
``kanibako setup``.

The check: for each locally built WHEEL (sdists are not compared), if PyPI already
serves that exact name+version, compare the CONTENT.  Identical is fine (a genuine
re-run).  Any difference means the version must be bumped, and we say so and exit
non-zero BEFORE the upload step can silently swallow it.

Deliberately compares extracted MEMBER CONTENT, not the archive bytes: wheels are
not byte-reproducible (zip entry timestamps differ per build), so an archive hash
would fail every time and get switched off within a week.  ``*.dist-info/WHEEL``
and ``RECORD`` are excluded for the same reason -- WHEEL embeds the build
toolchain's version, and RECORD is derived from the other members, so it can only
differ when they already do.  ``METADATA`` is deliberately KEPT: a dependency
floor changing under a fixed version is exactly the bug this is looking for.

Usage:  check-publish-collisions.py DIST_DIR
Exit:   0 nothing to flag  ·  1 a collision needs a version bump  ·  2 bad usage
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PYPI = "https://pypi.org/pypi"
SKIP_SUFFIXES = ("/WHEEL", "/RECORD")


def payload_digests(blob: bytes) -> dict[str, str]:
  """Map each wheel member to a sha256 of its CONTENT, minus the volatile ones."""
  out: dict[str, str] = {}
  with zipfile.ZipFile(io.BytesIO(blob)) as z:
    for name in z.namelist():
      if name.endswith("/") or name.endswith(SKIP_SUFFIXES):
        continue
      out[name] = hashlib.sha256(z.read(name)).hexdigest()
  return out


def parse_wheel_name(filename: str) -> tuple[str, str]:
  """``kanibako_agent_goose-0.4.0-py3-none-any.whl`` -> (dist-name, version)."""
  stem = filename[: -len(".whl")]
  name, version = stem.split("-")[:2]
  return name.replace("_", "-"), version


def published_wheel(name: str, version: str) -> bytes | None:
  """Return the published wheel's bytes, or None if that version is not on PyPI."""
  try:
    with urllib.request.urlopen(f"{PYPI}/{name}/{version}/json", timeout=30) as r:
      meta = json.load(r)
  except urllib.error.HTTPError as exc:
    if exc.code == 404:
      return None
    raise
  for url in meta.get("urls", []):
    if url["packagetype"] == "bdist_wheel":
      with urllib.request.urlopen(url["url"], timeout=60) as r:
        return r.read()
  return None


def main(argv: list[str]) -> int:
  if len(argv) != 2:
    print(__doc__, file=sys.stderr)
    return 2
  dist_dir = Path(argv[1])
  wheels = sorted(dist_dir.glob("*.whl"))
  if not wheels:
    print(f"No wheels in {dist_dir} — nothing to check.", file=sys.stderr)
    return 2

  collisions: list[str] = []
  for wheel in wheels:
    name, version = parse_wheel_name(wheel.name)
    remote = published_wheel(name, version)
    if remote is None:
      print(f"  ok    {name} {version}: new version, nothing published yet")
      continue

    local_d, remote_d = payload_digests(wheel.read_bytes()), payload_digests(remote)
    if local_d == remote_d:
      print(f"  ok    {name} {version}: already published, content identical")
      continue

    changed = sorted(
      k for k in set(local_d) | set(remote_d) if local_d.get(k) != remote_d.get(k)
    )
    collisions.append(name)
    print(f"  FAIL  {name} {version}: already published, CONTENT DIFFERS")
    for member in changed[:10]:
      if member not in remote_d:
        state = "added locally"
      elif member not in local_d:
        state = "removed locally"
      else:
        state = "modified"
      print(f"          {state:16} {member}")
    if len(changed) > 10:
      print(f"          … and {len(changed) - 10} more")

  if collisions:
    print(
      "\nERROR: the above package(s) would be published at a version PyPI "
      "already serves with DIFFERENT content.\n"
      "`skip-existing` would silently drop the upload and ship the OLD files.\n"
      "Bump the version in each package's pyproject.toml (and its __init__.py), "
      "raise the matching floor in packages/meta/pyproject.toml, and re-run.",
      file=sys.stderr,
    )
    return 1

  print("\nAll distributions are safe to publish.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
