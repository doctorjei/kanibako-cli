"""Thin reader of the shipped ``keyspace-manifest.yaml`` — the keyspace REGISTRY.

The manifest is the machine-readable projection of ``specs/settings-keyspace-1.8.0.md``:
one row per declared key, plus the category / bind-default / not-a-key tables.  It is
RELEASE AUTHORITY that ships in the wheel, and this module is the ONE way to read it.

⚑ IT DECLARES NOTHING AT RUNTIME.  Nothing on the launch or resolve path consults the
manifest: the keyspace's live carriers are the ``DECLARED_*`` frozensets in
:mod:`kanibako.settings.settings_keyspace` and the shipped ``core-defaults.yaml``.  The
manifest's job is to be asserted AGAINST those carriers (``tests/test_settings/
test_manifest_conformance.py``), which is why generating the frozensets FROM it was
measured and declined — see the manifest's own header.

⚑ CACHED, unlike :func:`kanibako.settings.core_defaults._load_doc`, and the difference
is not an oversight.  ``core-defaults.yaml`` carries live DEFAULTS whose tests
monkeypatch the reader, so re-reading per call is load-bearing there.  The manifest is
IMMUTABLE PACKAGED DATA — no code path writes it, no test rewrites it — and parsing its
3000-odd lines costs ~137 ms, so the parse is cached once per process.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from kanibako.settings.core_defaults import packaged_data_dir

# Filename of the shipped keyspace registry (in kanibako.data).
KEYSPACE_MANIFEST_FILENAME = "keyspace-manifest.yaml"


@lru_cache(maxsize=1)
def _parse_manifest() -> dict[str, Any]:
  """Parse the packaged manifest ONCE; the cached, never-handed-out original.

  ⚑ Read through :func:`~kanibako.settings.core_defaults.packaged_data_dir` — the ONE
  ``importlib.resources.files()`` join — so this resolves the INSTALLED package data,
  never a repo-relative path.  A conformance guard that read the checkout would be a
  statement about this working tree rather than about the artefact that ships.
  """
  ref = packaged_data_dir(KEYSPACE_MANIFEST_FILENAME)
  raw = yaml.safe_load(Path(str(ref)).read_text())
  if not isinstance(raw, dict):
    raise RuntimeError(
      f"{KEYSPACE_MANIFEST_FILENAME} is empty or is not a YAML mapping "
      f"(read from {ref}) — the keyspace registry is packaged data, so this "
      f"is a PACKAGING defect, not a configuration one"
    )
  return raw


def manifest_doc() -> dict[str, Any]:
  """The ratified keyspace manifest as a plain dict — a FRESH COPY every call.

  Copy-out at the boundary (P8): the parse is shared, the document is not.  A caller
  that mutated a shared dict would corrupt every later reader in the process, and the
  cache would make that corruption outlive the test that caused it.
  """
  return copy.deepcopy(_parse_manifest())
