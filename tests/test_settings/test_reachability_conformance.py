"""P — REACHABILITY: a declared key with a real default ANSWERS for a box that EXISTS.

The property, stated once:

  For every row in ``src/kanibako/data/keyspace-manifest.yaml``'s ``keys:`` table that
  (i) spells ONE CONCRETE key (no ``<placeholder>``) and (ii) declares a REAL-VALUED
  ``default:`` for box mode *m* — the arm for *m* being neither ``null``, nor ``{}``,
  nor a ``<prose placeholder>`` — the key ANSWERS in mode *m*: FOR ANY BOX THAT EXISTS
  IN MODE *m*, a whole-value ``@K`` reference resolves to a non-``__MISSING__``
  terminal.

⚑⚑ THE TERMINUS IS NOT A NAMED OBJECT.  It is ANY resolve the production path performs
for a box THAT ALREADY EXISTS — and K answers if it reaches a terminal in ANY of them.
Jei, 2026-08-29: *"termini - for any box thst exists, def."*, and on the previous
wording, *"'launch snapshot' sounds a little bit like a carve out…"*.  He is right:
naming ONE builder as *the* terminus privileges one snapshot object for no principled
reason, and a key that resolves at a different real resolve is answerable to a user
even though that wording called it dangling.

⚑ WHAT "A RESOLVE FOR A BOX THAT ALREADY EXISTS" MEANS, and it is a property of the
CALL SITE, not of the function.  A resolve is in scope when the production path reaches
it down a route that is NOT gated on the box being created.  Two consequences, and
neither is a skip-list:

* ``_resolve_launch_snapshot`` is in scope, because ``_run_container`` calls it on
  every launch of an already-registered box.
* the SAME function's NARROW seed call is OUT of scope, because its only caller is
  ``_seed_box_home``, and ``_seed_box_home``'s only two call sites are
  ``run_create`` and ``_run_container``'s ``if proj.is_new:`` block.  Both are box
  CREATION.  A box that exists never runs that resolve again and its result was
  discarded when it did, so a key that answers ONLY there is not answerable for an
  existing box — which is exactly the ruling above.  ``_sync_box_at_create`` is out
  for the identical reason and by the identical test.

⚑⚑ THE TERMINI ARE OBSERVED, NOT RESTATED.  :func:`_termini` wraps
``settings_launch.build_launch_snapshot`` with a recorder and then CALLS the production
resolvers; every snapshot collected is one the production code built with its own
inputs.  Nothing here re-derives a floor, re-assembles a cascade, or re-spells an
argument list into a second copy of a resolve — a reachability check that unioned the
floor builders would be the SECOND CARRIER of a shape ``settings/defaults_inventory.py``
already holds, which is the defect class this file exists to kill.

⚑ THREE THINGS P DELIBERATELY DOES NOT SAY, and each one is why this file needs no
discriminator, no allow-list and no exemption table:

* NOTHING ABOUT WHETHER ANYTHING READS K.  A *reserved* key — declared now, consumer
  later — satisfies P.  Jei's ruling on ``system.{backup,cache,runtime}`` ("reserved…
  something will go there eventually") is untouched by this file.  Reserved and
  reachable are ORTHOGONAL facts and P only ever asks the second.
* NOTHING ABOUT THE VALUE.  ``_PATH_ORACLE`` in
  ``tests/test_settings/test_manifest_conformance.py`` owns value correctness; P owns
  existence.  Two properties, two carriers, no overlap.
* NO EXEMPTION TABLE.  The exclusions in (i)/(ii) are read off each manifest row
  itself — the same mechanism ``tests/test_settings/test_set_column_conformance.py``
  uses for its ``set:`` column.  Declaring a key, retiring one, or filling in a
  ``null`` arm moves this file with NO edit here.  ⚑ AND NO PER-KEY DATA ANYWHERE:
  there is no key named in this file that gets treated differently from any other.
  One definition, applied identically to every row.

TERMINUS ROUTING, derived from the manifest and not special-cased:

* ``layer: 1`` rows → the resolve context's ``config`` map
  (``agent_select.launch_resolve_ctx(std, proj, agent).config``).  The manifest itself
  says Layer 1 is "resolved by the FLAT foundation (not the KeyStore pipeline)", and
  that foundation is built for every existing box on every launch.
* everything else → ANY recorded terminus, read with ``settings_launch.snapshot_leaf``,
  the ONE public dotted reader.

⚑ THE PROBE LAUNCHES WITH THE REAL ``claude`` TARGET, not ``NoAgentTarget`` — see
:func:`_probe`, which carries the measurement that forced the choice.  The no-agent
delta is REPORTED rather than hidden.

⚑ ONE HONEST GAP, stated rather than papered over.  Two resolves an existing box's
launch performs are not driven here: the image-share resolve (``start.py`` inside
``_add_image_share_mounts``) and the helper-hub resolve (inside the hub start).  Both
are CONDITIONAL and both need live host resources a unit-level probe cannot stand up —
a podman graph-root probe and a listening helper socket.  Both are narrow injections
(``include_base_families=False``) of one table, and the code says at both call sites
that "every row of it is already emitted by the main path", so neither can carry a key
the main resolve lacks; but that is the code's claim, not this file's measurement.

⚑ THIS FILE IS EXPECTED TO BE RED.  It measures the keyspace as it is; the findings it
reports are inputs to an approval-gated fix, not licence to move ``src/`` until it is
green.  Every ``print`` below is visible under ``pytest -s``.

Indent note: 2 spaces (the house style); ``tests/test_settings/`` carries both.
"""

from __future__ import annotations

import contextlib
import re

import pytest

from kanibako.project.workset import add_project, create_workset
from kanibako.settings.agent_select import AgentSelection, launch_resolve_ctx
from kanibako.settings.config import load_merged_config
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.paths import (
  WorksetSpec,
  box_workset_settings_paths,
  resolve_project,
  resolve_standalone_project,
  resolve_workset_project,
)
from kanibako.settings.settings_launch import snapshot_leaf
from kanibako.targets.no_agent import NoAgentTarget


# The three box modes, spelled as ``meta.box.mode`` spells them (``BoxMode``).
MODES = ("primary", "named", "standalone")

# MEASURED FLOORS, not targets.  They exist so this file reds on its own emptiness —
# a corpus that collapsed to nothing would otherwise pass every assertion below.
_CORPUS_FLOOR = {"primary": 45, "named": 45, "standalone": 38}
_DEMAND_EDGE_FLOOR = 30


# ---------------------------------------------------------------------------
# Fixtures: one resolved proj per mode.  ⚑ Declared locally, which is the house
# pattern — ``tests/test_channels/test_channel_keys.py`` carries the same three.
# ---------------------------------------------------------------------------

@pytest.fixture
def primary_proj(std, config, project_dir):
  return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def named_proj(std, config, tmp_home):
  ws_root = tmp_home / "worksets" / "my-set"
  workset = create_workset("my-set", ws_root, std)
  source = tmp_home / "original-project"
  source.mkdir()
  add_project(workset, "cool-app", source)
  return resolve_workset_project(
    WorksetSpec.from_workset(workset), "cool-app", std, config, initialize=True,
  )


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
  return resolve_standalone_project(std, config, str(project_dir), initialize=True)


@contextlib.contextmanager
def _recording():
  """Record every snapshot ``build_launch_snapshot`` produces while the block runs.

  ⚑ THE ONE MECHANISM THAT KEEPS THIS FILE FROM BECOMING A SECOND CARRIER.  Every
  resolve on the launch path funnels through this single builder — ``start.py`` calls
  it as ``settings_launch.build_launch_snapshot`` and ``config.py`` imports it inside
  the function body, so both bind the module attribute at CALL time and both are seen.
  The probe therefore never has to know what floor a resolve assembles or what
  arguments it forwards: it calls the production function and collects what that
  function's own pipeline built.
  """
  from kanibako.settings import settings_launch

  built: "list[object]" = []
  real = settings_launch.build_launch_snapshot

  def spy(*args, **kwargs):
    snapshot = real(*args, **kwargs)
    built.append(snapshot)
    return snapshot

  settings_launch.build_launch_snapshot = spy
  try:
    yield built
  finally:
    settings_launch.build_launch_snapshot = real


def _termini(std, config_file, proj, target):
  """Every terminus the production path produces FOR A BOX THAT ALREADY EXISTS.

  Returns ``[(label, snapshot), …]``; the label names the production entry point and
  its call site, so a finding can say WHERE a key answered.

  ⚑ IN-SCOPE IS DECIDED BY THE CALL SITE (see the module docstring): a resolve counts
  when the production path reaches it down a route that is NOT gated on the box being
  created.  Each driver below therefore carries the existing-box call site it stands
  for.  Nothing is skipped by name — the create-time resolves have no line here
  because no existing-box route reaches them, not because they were filtered out.

  ⚑ ARGUMENT SHAPE HELD CONSTANT: ``system_settings_path`` / ``agent_cfg_path`` are
  passed ``None`` throughout, exactly as this file's original single-terminus probe
  passed them (an absent file is an empty tier, which the resolvers document as an
  ordinary state).  Holding them fixed is what makes the before/after delta of this
  rewrite attributable to the TERMINUS change and to nothing else.

  ⚑ NOTHING IS SWALLOWED.  A driver that cannot stand up raises and reds the run; a
  ``try``/``except`` here would silently drop a terminus, which is a carve-out wearing
  an exception handler.
  """
  from kanibako.commands import start as start_cmd

  box_path, workset_path = box_workset_settings_paths(proj)
  # The §1A selection level a launch installs, built from the PRODUCTION dataclass
  # rather than hand-spelled — ``AgentSelection.selection_level`` is the only thing
  # that knows the shape (``{system.agent: node}``) and the no-agent ``None``.
  selection = AgentSelection(node="claude", source="settings").selection_level

  collected: "list[tuple[str, object]]" = []
  with _recording() as built:

    def drive(label: str, call) -> None:
      start_at = len(built)
      call()
      for offset, snapshot in enumerate(built[start_at:]):
        collected.append((f"{label}#{offset}", snapshot))

    # start.py:2424 — every launch loads the merged config before anything else,
    # and its box-scalar resolve (config._resolve_box_scalars) is a real resolve.
    drive("load_merged_config", lambda: load_merged_config(
      config_file, box_path, workset_path=workset_path, cli_overrides=None,
    ))
    # start.py:2749 / :3465 — the two focused agent-behavior resolves
    # (_effective_agent_scalar) a launch runs ahead of the main snapshot.
    drive("effective_bootstrap", lambda: start_cmd._effective_bootstrap(
      proj, None, "claude", agent_path=None,
    ))
    drive("effective_transform", lambda: start_cmd._effective_transform(
      proj, None, "claude", target, None,
    ))
    # start.py:2926, inside _run_container — the auth/decisions resolve.
    drive("box_launch_decisions", lambda: start_cmd._resolve_box_launch_decisions(
      std=std, proj=proj, target=target, agent_name="claude", agent_cfg=None,
      system_settings_path=None, agent_cfg_path=None, selection_level=selection,
    ))
    # stop.py:107 / launch/creds_watcher.py:338 — the same build for the TARGET-LESS
    # paths.  An existing box is what both of those act on, which is the whole test.
    drive("box_auth_source", lambda: start_cmd._resolve_box_auth_source(
      std=std, proj=proj, agent_name="claude",
      system_settings_path=None, agent_cfg_path=None, selection_level=selection,
    ))
    # start.py:3567, inside _run_container — the main launch resolve.
    drive("launch_snapshot", lambda: start_cmd._resolve_launch_snapshot(
      std=std, proj=proj, agent_name="claude",
      system_settings_path=None, agent_cfg_path=None,
      desc=None, install=None, target=target, agent_cfg=None,
    ))
  return collected


def _probe(request, std, config_file, mode: str, target=None):
  """``(termini, ctx)`` for *mode*, built off that mode's project and no other.

  ⚑ ``getfixturevalue`` rather than a three-project fixture ON PURPOSE: ``primary``
  and ``standalone`` both resolve the SAME ``project_dir``, so materialising both in
  one test would let one mode's ``initialize=True`` write into the other's reading.
  One test, one project, one mode.

  ⚑⚑ THE DEFAULT TARGET IS THE REAL ``claude`` PLUGIN, NOT ``NoAgentTarget`` — and
  that is a MEASUREMENT, not a preference.  Under ``NoAgentTarget`` three keys
  (``agent.default.{access,allow_helpers,continue_mode}``) report as dangling; under
  the real target all three answer.  A no-agent box is a legitimate launch but it is
  not the representative one, and asserting off it would MANUFACTURE a red.  The
  difference is not swept under the rug — it is the subject of
  :meth:`TestTheCorpusAndTheProbe.test_report_the_no_agent_delta`.

  ⚑ IT WAS FOUR UNDER THE SINGLE-TERMINUS PROBE, and the fourth is a result rather
  than a correction: ``agent.default.bootstrap`` now answers WITHOUT an agent target,
  because the ``_effective_bootstrap`` terminus floors it from ``_bootstrap_default()``
  — core's own declared value, not a plugin descriptor.  A key whose default comes
  from core was never really agent-dependent; the wider terminus set is what made that
  visible.
  """
  from kanibako.targets import resolve_target

  proj = request.getfixturevalue(f"{mode}_proj")
  termini = _termini(
    std, config_file, proj,
    resolve_target("claude", None) if target is None else target,
  )
  return termini, launch_resolve_ctx(std, proj, "claude")


# ---------------------------------------------------------------------------
# The corpus — derived off each row, never listed.
# ---------------------------------------------------------------------------

def _rows() -> "dict[str, dict]":
  """Every ``keys:`` row, parametric ones included (the demand graph reads those)."""
  return {
    str(key): row for key, row in manifest_doc()["keys"].items()
    if isinstance(row, dict)
  }


def _static_rows() -> "dict[str, dict]":
  """Rows whose spelling names ONE CONCRETE key — exclusion (i)."""
  return {key: row for key, row in _rows().items() if "<" not in key}


def _default_arm(row: dict, mode: str) -> object:
  """The REAL-VALUED default *row* declares for *mode*, or ``None`` for none.

  Exclusion (ii), read off the row: a missing ``default:``, a ``null`` arm, an empty
  ``{}`` category default and a ``<prose placeholder>`` each yield ``None``.  ⚑ The
  test is ``is None``, not falsiness — ``workset.auth.share_allowed`` declares a real
  ``False`` for standalone.
  """
  if "default" not in row:
    return None
  declared = row["default"]
  if isinstance(declared, dict):
    if mode not in declared:
      return None
    declared = declared[mode]
  if declared is None:
    return None
  if isinstance(declared, str) and "<" in declared:
    return None
  return declared


def _corpus(mode: str) -> "dict[str, dict]":
  """The rows P applies to in *mode*."""
  return {
    key: row for key, row in _static_rows().items()
    if _default_arm(row, mode) is not None
  }


def _answering_termini(key: str, row: dict, termini, ctx) -> "list[str]":
  """The labels of every terminus at which a whole-value ``@key`` reaches a terminal.

  ⚑ ANY, not a designated one — the ruled property.  A ``layer: 1`` row's terminus is
  the flat foundation, which the manifest itself says is not the KeyStore pipeline; it
  is built for every existing box on every launch, so it is one terminus among the
  rest rather than an exception to them.
  """
  if row.get("layer") == 1:
    return ["resolve_ctx.config"] if key in ctx.config else []
  return [
    label for label, snapshot in termini
    if snapshot_leaf(snapshot, key) is not __MISSING__
  ]


def _answers(key: str, row: dict, termini, ctx) -> bool:
  """Does a whole-value ``@key`` reference reach a terminal at ANY terminus?"""
  return bool(_answering_termini(key, row, termini, ctx))


def _partition(mode: str, termini, ctx) -> "tuple[list[str], list[str]]":
  """``(ANSWERS, DANGLES)`` for *mode*, both sorted."""
  answers, dangles = [], []
  for key, row in sorted(_corpus(mode).items()):
    (answers if _answers(key, row, termini, ctx) else dangles).append(key)
  return answers, dangles


# ---------------------------------------------------------------------------
# The demand graph — who @-references whom.
# ---------------------------------------------------------------------------

# Braced form first (it delimits the name explicitly); the bare form is GREEDY over
# dot-separated segments, exactly as the manifest's ``reference_forms`` says.
_BRACED_REF = re.compile(r"@\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}")
_BARE_REF = re.compile(r"@([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)")


def _refs_in(value: object) -> "set[str]":
  """Every ``@K`` / ``@{K}`` reference name spelled inside *value*."""
  if not isinstance(value, str):
    return set()
  found = set(_BRACED_REF.findall(value))
  found |= set(_BARE_REF.findall(_BRACED_REF.sub(" ", value)))
  return {name.rstrip(".") for name in found if name}


def _arms(value: object) -> "dict[str, object]":
  """*value* as a ``mode -> arm`` map; a scalar stands for all three modes."""
  if isinstance(value, dict):
    return {mode: value[mode] for mode in MODES if mode in value}
  return {mode: value for mode in MODES}


def _entry_defaults() -> "list[tuple[str, object]]":
  """Every ``bind_default_entries`` / ``category_default_entries`` entry default.

  Returned as ``(label, default)`` so a finding can name the entry it came from.
  """
  doc = manifest_doc()
  out: list[tuple[str, object]] = []
  for section in ("bind_default_entries", "category_default_entries"):
    for key, entries in (doc.get(section) or {}).items():
      if not isinstance(entries, dict):
        continue
      for dest, entry in entries.items():
        if isinstance(entry, dict) and "default" in entry:
          out.append((f"{section}[{key}][{dest}]", entry["default"]))
  return out


def _demand_edges() -> "list[tuple[str, str, str]]":
  """``(demander, mode, demanded_key)`` for every ``@``-reference in the manifest.

  Sources: every ``keys:`` row's ``default:`` AND ``value:`` (``meta.*`` rows carry
  the latter), plus every bind/category entry default.  A row referencing ITSELF is
  not an edge — P asks whether some OTHER declaration depends on the key.
  """
  static = _static_rows()
  edges: list[tuple[str, str, str]] = []

  def collect(label: str, value: object) -> None:
    for mode, arm in _arms(value).items():
      for name in _refs_in(arm):
        if name in static and name != label:
          edges.append((label, mode, name))

  for key, row in _rows().items():
    for field in ("default", "value"):
      if field in row:
        collect(key, row[field])
  for label, default in _entry_defaults():
    collect(label, default)
  return edges


# ---------------------------------------------------------------------------
# Families — derived two ways, no prefix guessing at scope level.
# ---------------------------------------------------------------------------

def _families() -> "dict[str, set[str]]":
  """``family label -> member keys``, built off the rows and closed over refs.

  Two derivations, and only two:

  * every ``layer: 1`` row is one family (the manifest's own Layer-1 block);
  * otherwise a key of depth >= 3 belongs to the family named by its parent prefix
    (``system.channels.broadcast`` → ``system.channels``).

  ⚑ DEPTH 2 IS THE SCOPE, NOT A FAMILY — which is exactly why ``system.backup``
  forms no family while ``system.channels.broadcast`` does.

  Closure adds any SAME-``scope:`` key a member's own default ``@``-references.
  ⚑ Cross-scope refs are NOT followed (``workset.channels.mailboxes`` →
  ``@system.channels.mailboxes``); follow them and every family merges into one blob.
  """
  static = _static_rows()
  families: dict[str, set[str]] = {}
  for key, row in static.items():
    if row.get("layer") == 1:
      families.setdefault("layer1", set()).add(key)
      continue
    segments = key.split(".")
    if len(segments) >= 3:
      families.setdefault(".".join(segments[:-1]), set()).add(key)

  for label, members in families.items():
    if label == "layer1":
      continue
    closed = set(members)
    for member in members:
      scope = static[member].get("scope")
      for arm in _arms(static[member].get("default")).values():
        for name in _refs_in(arm):
          if name in static and static[name].get("scope") == scope:
            closed.add(name)
    families[label] = closed
  return families


# ---------------------------------------------------------------------------
# 2a — corpus + probe, REPORT-ONLY.
# ---------------------------------------------------------------------------

class TestTheCorpusAndTheProbe:
  """The instrument, proved sound before anything is concluded from it."""

  def test_the_corpus_is_derived_and_non_empty(self):
    """Every mode's corpus clears a MEASURED floor — this file reds on emptiness."""
    sizes = {mode: len(_corpus(mode)) for mode in MODES}
    assert all(sizes[mode] >= _CORPUS_FLOOR[mode] for mode in MODES), (
      f"corpus collapsed: {sizes} against floor {_CORPUS_FLOOR}"
    )

  def test_every_skipped_row_proves_why_it_was_skipped(self):
    """No exemption table: each excluded row carries its own reason IN THE ROW."""
    unexplained: list[str] = []
    covered = {key for mode in MODES for key in _corpus(mode)}
    for key, row in _rows().items():
      if key in covered:
        continue
      if "<" in key:
        continue                                   # (i) parametric spelling
      if "default" not in row:
        continue                                   # (ii) declares no default at all
      declared = row["default"]
      arms = declared.values() if isinstance(declared, dict) else [declared]
      if isinstance(declared, dict) and not declared:
        continue                                   # (ii) `{}` — an empty category
      if all(
        arm is None or (isinstance(arm, str) and "<" in arm) for arm in arms
      ):
        continue                                   # (ii) null / prose placeholder
      unexplained.append(key)
    assert not unexplained, (
      "these rows fell out of the corpus without the row itself saying why:\n  "
      + "\n  ".join(sorted(unexplained))
    )

  def test_a_dict_default_is_always_mode_keyed(self):
    """Anti-vacuity for ``_default_arm``: a dict default is arms, or it is ``{}``."""
    stray = {
      key: sorted(row["default"])
      for key, row in _static_rows().items()
      if isinstance(row.get("default"), dict)
      and set(row["default"]) - set(MODES)
    }
    assert not stray, f"dict defaults keyed by something other than a mode: {stray}"

  @pytest.mark.parametrize("mode", MODES)
  def test_every_mode_yields_a_snapshot_and_something_answers(
    self, mode, request, std, config_file,
  ):
    """PER-TERMINUS anti-vacuity: every terminus is live, and the flat tier too.

    ⚑⚑ PER-TERMINUS, NOT GLOBAL, AND THAT IS THE POINT.  With one terminus, "something
    answers" was a sufficient floor.  With several, a global floor is satisfied by a
    SINGLE live terminus, so a bug that emptied every other one would still pass — the
    more termini P reads, the weaker a global floor gets.  Each recorded terminus must
    therefore reach a DECLARED key on its own, and the ``layer: 1`` flat foundation
    must be non-empty.

    ⚑ THE FLOOR IS "REACHES A DECLARED KEY", NOT "REACHES A CORPUS KEY", and that is a
    measurement rather than a softening.  A focused resolve legitimately carries ONE
    key: ``_effective_transform``'s snapshot holds ``agent.default.transform`` alone,
    whose declared default is ``null``, so it is outside the corpus by exclusion (ii)
    while the terminus itself is perfectly live.  A corpus-keyed floor would call that
    terminus broken and would have to be bought off with an exemption — the shape this
    file refuses.  ``_static_rows()`` is the whole declared keyspace, so an emptied
    terminus still reds and no key is named to make it pass.
    """
    termini, ctx = _probe(request, std, config_file, mode)
    assert termini, f"{mode}: the production path built NO snapshot at all"
    declared = _static_rows()
    corpus = _corpus(mode)
    inert = [
      label for label, snapshot in termini
      if not any(
        snapshot_leaf(snapshot, key) is not __MISSING__ for key in declared
      )
    ]
    assert not inert, (
      f"{mode}: these termini reach NO declared key at all — the probe, not the "
      f"keyspace, is wrong (or they do not belong in the set): {inert}"
    )
    assert any(
      key in ctx.config for key, row in corpus.items() if row.get("layer") == 1
    ), f"{mode}: the layer-1 flat foundation answers NOTHING"

    answers, dangles = _partition(mode, termini, ctx)
    assert answers, f"{mode}: NOTHING answers — the probe, not the keyspace, is wrong"
    print(f"\n=== P/REACHABILITY · mode={mode} ===")
    print(f"  corpus  {len(answers) + len(dangles)}")
    for label, snapshot in termini:
      reach = sum(
        1 for key in corpus if snapshot_leaf(snapshot, key) is not __MISSING__
      )
      print(f"  terminus {label}: {reach} corpus keys")
    print(f"  ANSWERS {len(answers)}: {', '.join(answers)}")
    print(f"  DANGLES {len(dangles)}: {', '.join(dangles) or '(none)'}")

  @pytest.mark.parametrize("mode", MODES)
  def test_report_where_each_key_answers(self, mode, request, std, config_file):
    """REPORT-ONLY: which terminus each answering key reached, by label.

    ⚑ The audit trail for the ruling.  A key that answers ONLY at a non-launch-snapshot
    terminus is precisely what the old single-terminus wording called dangling; naming
    the terminus is how that claim stays checkable instead of becoming a footnote.
    """
    termini, ctx = _probe(request, std, config_file, mode)
    print(f"\n=== WHERE EACH KEY ANSWERS · mode={mode} ===")
    for key, row in sorted(_corpus(mode).items()):
      where = _answering_termini(key, row, termini, ctx)
      print(f"  {key}: {', '.join(where) or 'NOWHERE'}")

  @pytest.mark.parametrize("mode", MODES)
  def test_report_the_no_agent_delta(self, mode, request, std, config_file):
    """REPORT-ONLY: what a ``NoAgentTarget`` launch reaches that an agent one does not.

    ⚑ THE INSTRUMENT'S OWN BIAS, MEASURED RATHER THAN ASSUMED.  A no-agent box
    installs no agent-tier floor, so keys that answer under the ``claude`` plugin
    dangle under ``NoAgentTarget``.  P is asserted off the agent-bearing launch; this
    case exists so the size of that choice is a number, not a footnote.
    """
    termini, ctx = _probe(request, std, config_file, mode)
    bare_termini, bare_ctx = _probe(
      request, std, config_file, mode, target=NoAgentTarget(),
    )
    with_agent = set(_partition(mode, termini, ctx)[0])
    without = set(_partition(mode, bare_termini, bare_ctx)[0])
    print(f"\n=== NoAgentTarget delta · mode={mode} ===")
    print(f"  answers ONLY with an agent target: {sorted(with_agent - without)}")
    print(f"  answers ONLY without one:          {sorted(without - with_agent)}")


# ---------------------------------------------------------------------------
# 2a′ — P ITSELF, ASSERTED.  Everything above this line REPORTS P; nothing
#       required it.  D and F below are narrower properties, not substitutes.
# ---------------------------------------------------------------------------

class TestEveryDeclaredDefaultAnswers:
  """P, asserted directly: no key in the corpus dangles, in any mode.

  ⚑⚑ THIS CASE DID NOT EXIST UNTIL 2026-08-29, AND ITS ABSENCE IS THE WHOLE REASON THE
  DEFECT CLASS SURVIVED.  ``[R143]`` ratified P and the spec carries it, but the only
  P-side check in this file was ANTI-VACUITY — *something* answers.  D (demand) and F
  (family closure) were written as PROXIES while P was still going to be narrowed to a
  fallback; when P was ratified outright, the proxies stayed and the property itself was
  never pinned.  **A file can be green on two proxies while the property they proxy for
  is false**, and it was: ``box.enable_vault`` answers NOWHERE and reds nothing, because
  no declaration ``@``-references it (so D is silent) and it is in no partially-installed
  family (so F is silent).

  🛑 DO NOT WEAKEN THIS TO "the demanded ones" OR "the ones in a family".  Those are D
  and F, they already exist below, and each is satisfiable while P fails.  P asks the
  only question the manifest actually promises: a declared default RESOLVES.

  ⚑ NO EXEMPTION SET, BY CONSTRUCTION.  The corpus comes from the manifest rows
  themselves (exclusions (i) and (ii) are read off each row); nothing here names a key.
  Declaring a key, retiring one, or filling in a ``null`` arm moves this case with no
  edit — which is what makes it a property rather than a list.
  """

  @pytest.mark.parametrize("mode", MODES)
  def test_no_declared_default_dangles(self, mode, request, std, config_file):
    """Every corpus key ANSWERS at some resolve a box that already exists performs."""
    termini, ctx = _probe(request, std, config_file, mode)
    _answers, dangles = _partition(mode, termini, ctx)
    assert not dangles, (
      f"mode={mode}: these keys declare a real default that NOTHING installs, so a "
      f"whole-value @-reference to each resolves absent for a box that already "
      f"exists:\n" + "\n".join(
        f"  {key} (default: {_corpus(mode)[key].get('default')!r})" for key in dangles
      )
    )


# ---------------------------------------------------------------------------
# 2b — the demand graph.  A NARROWER property than P above: it catches a
#      dangling key EARLIER, by naming who breaks when it dangles.
# ---------------------------------------------------------------------------

class TestNoDemandedKeyDangles:
  """A key ANOTHER declaration ``@``-references must answer, or that one collapses."""

  def test_the_graph_is_non_empty(self):
    edges = _demand_edges()
    print(f"\n=== demand graph: {len(edges)} edges, "
          f"{len({key for _d, _m, key in edges})} distinct demanded keys ===")
    assert len(edges) >= _DEMAND_EDGE_FLOOR, (
      f"demand graph collapsed to {len(edges)} edges (floor {_DEMAND_EDGE_FLOOR})"
    )

  def test_known_edges_are_present(self):
    """Anti-vacuity: three edges read straight off the manifest must be found."""
    edges = {(demander, key) for demander, _mode, key in _demand_edges()}
    expected = {
      # the manifest's own formula for the broadcast leaf
      ("workset.channels.broadcast", "workset.channels.chat"),
      # the helper-socket bind entry, ``bind_default_entries``
      ('bind_default_entries[box.bindings.rw][~/.kanibako/state/helper.sock]',
       "system.runtime"),
      # a ``meta.*`` row's ``value:``, which is where meta declarations live
      ("meta.box.workspace", "workset.workspaces"),
    }
    assert expected <= edges, f"missing demand edges: {sorted(expected - edges)}"

  @pytest.mark.parametrize("mode", MODES)
  def test_no_demanded_key_dangles(self, mode, request, std, config_file):
    """⚑ EXPECTED RED. A demanded key that dangles makes its demander collapse."""
    termini, ctx = _probe(request, std, config_file, mode)
    corpus = _corpus(mode)
    demanders: dict[str, set[str]] = {}
    for demander, edge_mode, key in _demand_edges():
      if edge_mode == mode and key in corpus:
        demanders.setdefault(key, set()).add(demander)
    findings = [
      f"  {key} <- demanded by {', '.join(sorted(who))}"
      for key, who in sorted(demanders.items())
      if not _answers(key, corpus[key], termini, ctx)
    ]
    assert not findings, (
      f"mode={mode}: these keys are @-referenced by another declaration and do NOT "
      f"answer at ANY terminus, so every reference to them resolves absent:\n"
      + "\n".join(findings)
    )

  @pytest.mark.parametrize("mode", MODES)
  def test_report_demands_on_keys_with_no_default_for_this_mode(self, mode):
    """REPORT-ONLY: a demand P cannot judge, because the arm declares no default."""
    corpus = _corpus(mode)
    static = _static_rows()
    outside = sorted({
      f"{key} <- {demander}"
      for demander, edge_mode, key in _demand_edges()
      if edge_mode == mode and key not in corpus and key in static
    })
    print(f"\n=== demands outside the corpus · mode={mode} ({len(outside)}) ===")
    for line in outside:
      print(f"  {line}")


# ---------------------------------------------------------------------------
# 2c — family closure.
# ---------------------------------------------------------------------------

class TestNoFamilyIsPartiallyInstalled:
  """A family is installed WHOLE or it is reserved — never half."""

  def test_the_families_are_derived_and_expected_ones_exist(self):
    """Anti-vacuity, and the depth rule stated as a measurement."""
    families = _families()
    assert "layer1" in families and len(families["layer1"]) == 6
    assert "system.channels" in families
    assert "workset.channels" in families
    # ⚑ Depth 2 is the SCOPE: ``system.backup`` forms no family of its own.
    assert not any(
      label != "layer1" and len(label.split(".")) < 2 for label in families
    )
    assert "system.backup" not in families

  def test_the_closure_does_not_cross_scopes(self):
    """⚑ Follow a cross-scope ref and every family merges into one blob."""
    static = _static_rows()
    for label, members in _families().items():
      if label == "layer1":
        continue
      scopes = {static[member].get("scope") for member in members}
      assert len(scopes) == 1, f"{label} closed across scopes {scopes}: {sorted(members)}"

  @pytest.mark.parametrize("mode", MODES)
  def test_no_family_is_half_installed(self, mode, request, std, config_file):
    """A family where NONE answers is reserved-shaped — reported, not red."""
    termini, ctx = _probe(request, std, config_file, mode)
    corpus = _corpus(mode)
    partial: list[str] = []
    reserved: list[str] = []
    for label, members in sorted(_families().items()):
      applicable = sorted(member for member in members if member in corpus)
      if not applicable:
        continue
      answering = [
        member for member in applicable
        if _answers(member, corpus[member], termini, ctx)
      ]
      if not answering:
        reserved.append(f"{label} ({len(applicable)} members)")
      elif len(answering) < len(applicable):
        missing = [member for member in applicable if member not in answering]
        partial.append(f"  {label}: answers {answering}; DANGLES {missing}")
    print(f"\n=== families where NOTHING answers (reserved-shaped) · mode={mode} ===")
    for line in reserved or ["  (none)"]:
      print(f"  {line}")
    assert not partial, (
      f"mode={mode}: these families are HALF installed — some members reach the "
      f"terminus and their siblings do not:\n" + "\n".join(partial)
    )


# ---------------------------------------------------------------------------
# 2d — the residue report.  ASSERTS NOTHING.
# ---------------------------------------------------------------------------

class TestTheResidueReport:
  """The set only the user can rule on: undemanded, family-less, dangling."""

  @pytest.mark.parametrize("mode", MODES)
  def test_report_the_residue(self, mode, request, std, config_file):
    """REPORT-ONLY, deliberately. No assertion is possible here without a ruling.

    ⚑ ``demanded`` is scoped TO THIS MODE, matching the 2b assertion.  Mode-blind, a
    key demanded only by another mode's arm (``workset.kuid``, reached from
    ``meta.box.name``'s STANDALONE arm alone) would silently vanish out of the
    primary/named residue it belongs in.
    """
    families = _families()
    in_a_family = {member for members in families.values() for member in members}
    demanded = {
      key for _demander, edge_mode, key in _demand_edges() if edge_mode == mode
    }
    termini, ctx = _probe(request, std, config_file, mode)
    corpus = _corpus(mode)
    residue = sorted(
      key for key, row in corpus.items()
      if key not in demanded
      and key not in in_a_family
      and not _answers(key, row, termini, ctx)
    )
    print(f"\n=== RESIDUE — undemanded, family-less, DANGLING · mode={mode} ===")
    for key in residue or ["(none)"]:
      print(f"  {key}")
    print(f"  ({len(residue)} of {len(corpus)} corpus rows)")
