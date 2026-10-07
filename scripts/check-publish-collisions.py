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

The check: for each locally built wheel and sdist, if PyPI already serves that
exact name+version, compare the CONTENT.  Identical is fine (a genuine re-run).
Any difference means the version must be bumped, and we say so and exit non-zero
BEFORE the upload step can silently swallow it.

Deliberately compares extracted MEMBER CONTENT, not the archive bytes: neither
format is byte-reproducible (zip entry timestamps, tar headers and the gzip header
differ per build), so an archive hash would fail every time and get switched off
within a week.  Sdist members are keyed without their ``<name>-<version>/`` top
directory, whose spelling changed with setuptools' name normalization.  Skipped:
``*.dist-info/WHEEL`` and ``RECORD`` -- WHEEL embeds the build toolchain's version,
and RECORD is derived from the other members, so it can only differ when they
already do -- and the sdist's ``PKG-INFO`` (top-level and ``*.egg-info/``), which
the toolchain renders (setuptools 68 writes ``Metadata-Version: 2.1`` and drops
every ``Requires-Dist``).  ``METADATA``, ``pyproject.toml`` and ``requires.txt`` are
deliberately KEPT: a dependency floor changing under a fixed version is exactly the
bug this is looking for.

Usage:  check-publish-collisions.py DIST_DIR
Exit:   0 nothing to flag  ·  1 a collision needs a version bump  ·  2 bad usage
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

PYPI = "https://pypi.org/pypi"

#: Toolchain-written members, never compared (see the module docstring): the
#: wheel's build stamp and its derived RECORD, and the sdist's rendered PKG-INFO.
SKIP_MEMBERS = ("*.dist-info/WHEEL", "*.dist-info/RECORD", "PKG-INFO", "*.egg-info/PKG-INFO")


def payload_digests(blob: bytes) -> dict[str, str]:
  """Map each wheel or sdist member to a sha256 of its CONTENT, minus the skipped ones.

  Regular files only; an sdist member's key drops its ``<name>-<version>/`` prefix.
  """
  members: dict[str, bytes] = {}
  if zipfile.is_zipfile(io.BytesIO(blob)):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
      for name in z.namelist():
        if not name.endswith("/"):
          members[name] = z.read(name)
  else:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
      for m in t.getmembers():
        f = t.extractfile(m) if m.isfile() else None
        if f is not None:
          members[m.name.split("/", 1)[-1]] = f.read()
  return {
    name: hashlib.sha256(data).hexdigest()
    for name, data in members.items()
    if not any(fnmatchcase(name, pat) for pat in SKIP_MEMBERS)
  }


def parse_dist_name(filename: str) -> tuple[str, str, str]:
  """``kanibako_agent_goose-0.4.0-py3-none-any.whl`` -> (dist-name, version, packagetype).

  An sdist is ``<name>-<version>.tar.gz``; setuptools before 69 kept the hyphens in
  ``<name>``, so the version is whatever follows the LAST hyphen.
  """
  if filename.endswith(".whl"):
    name, version = filename[: -len(".whl")].split("-")[:2]
    kind = "bdist_wheel"
  else:
    name, version = filename[: -len(".tar.gz")].rsplit("-", 1)
    kind = "sdist"
  return name.replace("_", "-"), version, kind


def published_artifacts(name: str, version: str, kinds: set[str]) -> dict[str, bytes] | None:
  """Return the published bytes of each wanted packagetype, or None if that version is not on PyPI.

  One JSON request per name+version; a kind PyPI does not serve is absent from the result.
  """
  try:
    with urllib.request.urlopen(f"{PYPI}/{name}/{version}/json", timeout=30) as r:
      meta = json.load(r)
  except urllib.error.HTTPError as exc:
    if exc.code == 404:
      return None
    raise
  found: dict[str, bytes] = {}
  for url in meta.get("urls", []):
    kind = url["packagetype"]
    if kind in kinds and kind not in found:
      with urllib.request.urlopen(url["url"], timeout=60) as r:
        found[kind] = r.read()
  return found


def main(argv: list[str]) -> int:
  if len(argv) != 2:
    print(__doc__, file=sys.stderr)
    return 2
  dist_dir = Path(argv[1])
  artifacts = sorted([*dist_dir.glob("*.whl"), *dist_dir.glob("*.tar.gz")])
  if not artifacts:
    print(f"No wheels or sdists in {dist_dir} — nothing to check.", file=sys.stderr)
    return 2

  releases: dict[tuple[str, str], dict[str, Path]] = {}
  for path in artifacts:
    name, version, kind = parse_dist_name(path.name)
    releases.setdefault((name, version), {})[kind] = path

  collisions: list[str] = []
  for (name, version), local in releases.items():
    remote = published_artifacts(name, version, set(local))
    for kind, path in sorted(local.items()):
      if remote is None or kind not in remote:
        print(f"  ok    {path.name}: new artifact, nothing published yet")
        continue

      local_d, remote_d = payload_digests(path.read_bytes()), payload_digests(remote[kind])
      if local_d == remote_d:
        print(f"  ok    {path.name}: already published, content identical")
        continue

      changed = sorted(
        k for k in set(local_d) | set(remote_d) if local_d.get(k) != remote_d.get(k)
      )
      collisions.append(path.name)
      print(f"  FAIL  {path.name}: already published, CONTENT DIFFERS")
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

  print("\nEvery wheel and sdist is safe to publish.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv))
