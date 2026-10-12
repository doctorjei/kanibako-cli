"""Thin reader of the shipped ``keyspace-manifest.yaml`` — the keyspace REGISTRY.

It is the machine-readable projection of ``specs/settings-keyspace-1.8.0.md``, ships in
the wheel as RELEASE AUTHORITY, and this module is the ONE way to read it.

⚑ IT DECLARES NOTHING AT RUNTIME; nothing on the launch or resolve path consults the
manifest.  The keyspace's live carriers are the ``DECLARED_*`` frozensets and
``core-defaults.yaml``; the manifest is asserted AGAINST them
(``tests/test_settings/test_manifest_conformance.py``), never generated FROM.

⚑ THE PARSE IS CACHED, as :func:`kanibako.settings.core_defaults._parse_doc` is.
Reasoning for the cache, the declined generation and the copy-out:
``llm-docs/kanibako/settings/keyspace_manifest.py.md``.
"""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any

from kanibako.settings.config_io import load_packaged_mapping
from kanibako.settings.core_defaults import PACKAGED_SETTINGS_PARTS, packaged_data_dir

# Filename of the shipped keyspace registry (under ``PACKAGED_SETTINGS_PARTS``).
KEYSPACE_MANIFEST_FILENAME = "keyspace-manifest.yaml"


@lru_cache(maxsize=1)
def _parse_manifest() -> dict[str, Any]:
  """Parse the packaged manifest ONCE; the cached, never-handed-out original.

  ⚑ Resolves the INSTALLED package data through
  :func:`~kanibako.settings.core_defaults.packaged_data_dir`, never a repo-relative path:
  a guard that read the checkout would describe this tree, not the wheel that ships.
  """
  ref = packaged_data_dir(*PACKAGED_SETTINGS_PARTS, KEYSPACE_MANIFEST_FILENAME)
  return load_packaged_mapping(ref, KEYSPACE_MANIFEST_FILENAME)


def manifest_doc() -> dict[str, Any]:
  """The living manifest of the ratified keyspace — a plain dict, FRESH COPY every call (P8)."""
  return copy.deepcopy(_parse_manifest())
