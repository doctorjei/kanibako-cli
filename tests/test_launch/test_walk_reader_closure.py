"""Each reader bound for ``kickstart/_registry`` and ``_walk``, in its own body, takes no early
record and loads no ``workset.yaml`` (bootstrap design § 4, *The early set is empty*; § 8 row EP).

An AST scan: only executed imports would show at runtime, and
a ``workset.yaml`` load on a rare arm would not.  The Layer 2 wrappers around them
(``find_connected_external_box``) keep their reads.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from kanibako.launch import box_resolve
from kanibako.project import names, workset_registry
from kanibako.settings import paths

#: Names whose use means an early record or a ``workset.yaml`` load.
_FORBIDDEN_NAMES = frozenset(
  {
    "EarlyScope",
    "EarlySystem",
    "early_system",
    "_early_scope",
    "WORKSET_META_FILE",
    "load_workset",
    "_load_workset",
    "load_workset_settings_doc",
    "resolve_workspaces_locator",
  }
)
#: Modules a reader must not import.
_FORBIDDEN_MODULES = ("kanibako.channels", "kanibako.settings.workset_dirkeys")
_FORBIDDEN_PARAMS = frozenset({"early", "early_system"})

_READERS = [
  (names, "_load"),
  (names, "read_names"),
  (names, "lookup_by_path"),
  (names, "_workset_member_paths"),
  (names, "resolve_name"),
  (names, "_space_member"),
  (names, "_primary_member"),
  (names, "_refuse_other_space_member"),
  (names, "resolve_qualified_name"),
  (workset_registry, "_load_raw"),
  (workset_registry, "_load_boxes_raw"),
  (workset_registry, "load_workset_boxes"),
  (workset_registry, "workset_box_path"),
  (workset_registry, "reverse_lookup_workset_box"),
  (workset_registry, "workset_registry_path"),
  (box_resolve, "_find_owning_box"),
  (box_resolve, "find_connected_member"),
  (box_resolve, "path_space_worksets"),
  (box_resolve, "named_path_space"),
  (box_resolve, "_path_space_workset"),
  (paths, "_find_local_ancestor"),
  (paths, "load_primary_boxes"),
  (paths, "designation_route"),
  (paths, "resolve_designation"),
]


def _offenses(func: object) -> list[str]:
  tree = ast.parse(textwrap.dedent(inspect.getsource(func)))  # type: ignore[arg-type]
  found: list[str] = []
  for node in ast.walk(tree):
    if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
      found.append(node.id)
    elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
      found.append(node.attr)
    elif isinstance(node, ast.keyword) and node.arg in _FORBIDDEN_NAMES:
      found.append(f"{node.arg}=")
    elif isinstance(node, ast.arg) and node.arg in _FORBIDDEN_PARAMS:
      found.append(f"parameter {node.arg}")
    elif isinstance(node, ast.ImportFrom) and node.module is not None:
      if node.module.startswith(_FORBIDDEN_MODULES):
        found.append(f"import {node.module}")
      found.extend(a.name for a in node.names if a.name in _FORBIDDEN_NAMES)
    elif isinstance(node, ast.Import):
      found.extend(f"import {a.name}" for a in node.names if a.name.startswith(_FORBIDDEN_MODULES))
  return found


@pytest.mark.parametrize(
  ("module", "name"), _READERS, ids=[f"{m.__name__.rsplit('.', 1)[-1]}.{n}" for m, n in _READERS]
)
def test_reader_takes_no_early_record_and_loads_no_workset_yaml(module, name):
  assert _offenses(getattr(module, name)) == []


def test_the_layer2_wrapper_still_reads_workspaces():
  """The scan sees a real load: the wrapper that keeps one is caught."""
  assert "WORKSET_META_FILE" in _offenses(box_resolve.find_connected_external_box)
