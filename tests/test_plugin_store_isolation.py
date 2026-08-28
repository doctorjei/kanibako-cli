"""PLUGINS NEVER TOUCH A KEY STORE — the closure argument, pinned as an assertion.

A plugin's job is to describe an agent: a ``Target`` subclass, a defaults YAML, a
seed tree.  It reaches the settings system through the resolved values handed to
it, never through the store object those values live in.  That has always been
TRUE, and it was only ever a GREP — a measurement of one moment, with nothing
stopping the next plugin from reopening the gap silently.  This file converts it
into a standing assertion over plugin SOURCE.

WHY A STATIC SCAN, AND WHAT IT ADDS TO THE CENSUS
-------------------------------------------------
``tests/_keystore_census.py`` is the runtime arm: it patches ``KeyStore``'s write
funnel, records every key written during a session, and fails the run on an
undeclared one.  It is the authority on WHAT IS WRITTEN, and this file does not
judge key names at all — for that question, go there.

But the census names its own hole, in its KNOWN BLIND SPOTS list: *"``packages/*/
tests`` do not load ``tests/conftest.py``, so plugin-side writes are not censused
unless this plugin is registered there too."*  It is a pytest plugin, so it can
only see code that some session actually imports and runs.  The scan below needs
no conftest, no import and no execution — it reads the plugin trees off disk — so
it holds over exactly the code the census cannot watch.  The two are one
argument in two halves: *plugin source never names a store* (here), and *every
key a store does receive is declared* (the census).

WHAT COUNTS AS TOUCHING ONE — the rule, stated so a future reader can apply it
-----------------------------------------------------------------------------
* **Python only.**  Touching a store is an act — an import, a call, an attribute
  reach — and only Python performs one.  A defaults YAML cannot import anything,
  so it is not in the corpus.  (The one place ``keystore`` appears anywhere under
  ``packages/*/src`` today is a COMMENT in
  ``agent-claude/.../claude-defaults.yaml`` explaining why a key kept its name.
  It is out of the corpus twice over: not Python, and not code.)
* **Read through the AST, never as text.**  A comment or a docstring never
  becomes an identifier node, so prose about key stores stays free — including
  this docstring, which is inside the corpus' sibling directory and would light
  up any grep-based version of this test.
* **Exact names, not substrings.**  ``keystore_strings`` is a message table, not
  a store, and matching on substrings would refuse it for no reason.
* **Three forms, because one alone is evasion-shaped**: a static ``import``, a
  bare reference to a store name, and a store's dotted path sitting in a
  non-docstring string constant (what ``importlib.import_module`` takes).

⚑ A red here is not fixed by relaxing the scan.  It means a plugin acquired a
route into the settings system that bypasses the resolved values it is supposed
to be handed, and the route is the bug.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.repo import REPO_ROOT

#: The store modules, by dotted path — what a dynamic import would name.
STORE_MODULES = frozenset({
  "kanibako.settings.keystore",
  "kanibako.settings.kb_store",
})

#: The store names, matched EXACTLY as identifiers: the two module leaves and the
#: class itself.  ``keystore_strings`` is deliberately absent — it is a message
#: table, and an exact-match set is what keeps it out.
STORE_NAMES = frozenset({"KeyStore", "keystore", "kb_store"})

#: Plugin distributions that must be in the corpus.  A SUBSET check, not equality:
#: a fourth plugin should be scanned the day it lands, but a missing one of these
#: means the glob stopped finding what it was written to find.
EXPECTED_PLUGINS = frozenset({"agent-claude", "agent-codex", "agent-goose"})

#: Directory names that are build output rather than shipped source.  ``build/``
#: holds stale wheel copies and ``.claude/`` may hold another agent's live
#: worktree; neither is a plugin's source and a hit in one would be a phantom.
_NOT_SOURCE = frozenset({"build", ".claude", "__pycache__"})


def _plugin_sources(root: Path) -> dict[str, list[Path]]:
  """Every Python file each plugin distribution ships, keyed by distribution name.

  Anchored at ``packages/*/src`` — the tree that becomes the wheel — so a
  distribution's tests, its ``pyproject.toml`` and its ``build/`` output are all
  outside by construction.  ``data/`` payload scripts ARE inside: they ship in the
  wheel and get seeded into a box, and nothing shipped by a plugin has business
  reaching for a store.  Scanning them costs nothing and closes a place an
  exclusion could later be hidden behind.

  ⚑ Deliberately not shared with ``test_plugin_import_compat._shipped_sources``,
  which enumerates core AND plugin source as one flat list.  This one must group
  BY DISTRIBUTION, because that grouping is what the vacuity pin below reads.
  """
  found: dict[str, list[Path]] = {}
  for src in sorted((root / "packages").glob("*/src")):
    files = [
      path for path in sorted(src.rglob("*.py"))
      if not _NOT_SOURCE & set(path.parts)
    ]
    if files:
      found[src.parent.name] = files
  return found


def _docstring_constants(tree: ast.Module) -> set[int]:
  """The ids of every Constant node that is a module/class/function docstring."""
  ids: set[int] = set()
  holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
  for node in ast.walk(tree):
    if not isinstance(node, holders) or not node.body:
      continue
    first = node.body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
      if isinstance(first.value.value, str):
        ids.add(id(first.value))
  return ids


def _store_references(path: Path) -> list[str]:
  """Every place *path* names a key store, as ``file:line: form -- what``."""
  source = path.read_text(encoding="utf-8")
  try:
    tree = ast.parse(source, filename=str(path))
  except SyntaxError as exc:  # a shipped .py that will not parse is its own defect
    pytest.fail(f"{path} does not parse: {exc}")
  docstrings = _docstring_constants(tree)
  hits: list[str] = []

  def note(node: ast.AST, form: str, what: str) -> None:
    hits.append(f"{path.name}:{getattr(node, 'lineno', '?')}: {form} -- {what}")

  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      for alias in node.names:
        if alias.name in STORE_MODULES:
          note(node, "import", alias.name)
    elif isinstance(node, ast.ImportFrom):
      if node.module in STORE_MODULES:
        note(node, "from-import", str(node.module))
      else:
        for alias in node.names:
          # ``from kanibako.settings import keystore`` — the module as a NAME.
          if alias.name in STORE_NAMES:
            note(node, "from-import", f"{node.module}.{alias.name}")
    elif isinstance(node, ast.Name) and node.id in STORE_NAMES:
      note(node, "name", node.id)
    elif isinstance(node, ast.Attribute) and node.attr in STORE_NAMES:
      note(node, "attribute", node.attr)
    elif isinstance(node, ast.Constant) and isinstance(node.value, str):
      # A dotted store path in a live string is a dynamic import; in a DOCSTRING
      # it is prose, and prose about the settings system is not a defect.
      if node.value in STORE_MODULES and id(node) not in docstrings:
        note(node, "string", node.value)
  return hits


def _offenders(root: Path) -> list[str]:
  """Every store reference in every plugin source under *root*."""
  return [
    f"{dist}/{hit}"
    for dist, files in _plugin_sources(root).items()
    for path in files
    for hit in _store_references(path)
  ]


### The pin ###

def test_no_plugin_source_touches_a_key_store() -> None:
  """No shipped plugin module imports, names, or dynamically loads a key store."""
  offenders = _offenders(REPO_ROOT)
  assert not offenders, (
    "plugin source reached for a key store:\n  "
    + "\n  ".join(offenders)
    + "\n\nA plugin describes an agent; it receives RESOLVED settings values and "
      "has no business holding the store they came out of. Take the value from "
      "the resolved settings it is handed, not the store."
  )


### Anti-vacuity: the scan has to be looking at something ###

def test_every_expected_plugin_is_in_the_corpus() -> None:
  """A wrong glob or a renamed tree must RED, not pass by finding nothing.

  The assertion above is a negative, and a negative over an empty set is free.
  This is the denominator: the three first-party plugin distributions, each
  contributing real files, with ``target.py`` present as the anchor that says the
  scan reached the plugin package itself and not just some outer directory.
  """
  found = _plugin_sources(REPO_ROOT)
  missing = EXPECTED_PLUGINS - set(found)
  assert not missing, (
    f"the plugin scan found {sorted(found)} and missed {sorted(missing)} — the "
    f"negative it asserts is worthless over a corpus that lost its files."
  )
  for dist in sorted(EXPECTED_PLUGINS):
    names = {path.name for path in found[dist]}
    assert "target.py" in names, f"{dist} contributed {sorted(names)}, no target.py"


def test_the_scanner_catches_a_planted_reference(tmp_path: Path) -> None:
  """Mutation proof, run every session: a planted store use in each form is caught.

  ⚑ Planted in a FAKE tree under ``tmp_path``, never in a tracked plugin file.
  The whole pipeline runs — enumerate, parse, judge — so this fails if the glob,
  the name sets or the walk is ever weakened, which is what stops the pin above
  from decaying into a test that cannot fail.
  """
  pkg = tmp_path / "packages" / "agent-fake" / "src" / "kanibako" / "plugins" / "fake"
  pkg.mkdir(parents=True)
  (pkg / "target.py").write_text(
    "from kanibako.settings.keystore import KeyStore\n"   # from-import, dotted module
    "import kanibako.settings.kb_store\n"                 # import
    "from kanibako.settings import kb_store\n"            # from-import, module as name
    "import importlib\n"
    "mod = importlib.import_module('kanibako.settings.keystore')\n"  # string
    "store = KeyStore({})\n"                              # name
    "held = mod.keystore\n",                              # attribute
    encoding="utf-8",
  )
  offenders = _offenders(tmp_path)
  forms = {hit.split(": ", 1)[1].split(" -- ")[0] for hit in offenders}
  assert forms == {"import", "from-import", "name", "string", "attribute"}, offenders


def test_a_mention_in_prose_is_not_a_reference(tmp_path: Path) -> None:
  """The other half of the proof: prose about a key store must NOT red the suite.

  A scan that fired on comments would be un-livable — the live corpus already
  carries such a comment, in ``claude-defaults.yaml`` — and a maintainer's first
  move against a spurious red is to weaken the scan. This pins that they never
  have to.
  """
  pkg = tmp_path / "packages" / "agent-fake" / "src" / "kanibako" / "plugins" / "fake"
  pkg.mkdir(parents=True)
  # ⚑ The two docstrings are the BARE dotted paths on purpose. A string constant
  # that IS a store path is the one thing the string rule fires on, so writing the
  # pathological input is what actually exercises the docstring exclusion; a
  # docstring merely CONTAINING the path would pass for the uninteresting reason
  # that the match is exact.
  (pkg / "target.py").write_text(
    '"""kanibako.settings.keystore"""\n'
    "# A KeyStore is not ours to hold; keystore and kb_store stay in core.\n"
    "def go() -> None:\n"
    '  """kanibako.settings.kb_store"""\n'
    "  return None\n",
    encoding="utf-8",
  )
  (pkg / "fake-defaults.yaml").write_text(
    "# Key name kept `managed_pointer` to avoid a keystore-key clash.\nagent: {}\n",
    encoding="utf-8",
  )
  assert _offenders(tmp_path) == []
