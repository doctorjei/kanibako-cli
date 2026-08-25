"""§2a DECLARATION ROOTS, driven from a real SETTINGS FILE (not a helper in isolation).

Every case here writes a settings file, runs the production path a launch runs —
``assemble_levels`` (inside ``build_launch_snapshot``) → ``snapshot_category_entries`` —
and reads the ``host_src`` that comes out the far end, which is the string
``commands.start._emit_category_mounts`` hands to ``Path()`` and then to podman.

TWO HALVES, and they are separate rules:

* the ABSTRACT categories (``common`` / ``caches`` / ``seeded``) let an author write a
  bare LEAF, and the root is supplied AT DECLARATION-LOAD, so what is STORED is the full
  self-resolving ``@``-ref. Rooted at DECLARATION is REQUIRED; a later layer prepending a
  root is the ``scope_roots`` shape §2a calls FORBIDDEN — which is why these assertions
  read the source off the FILE path and not off an injected floor entry.
* the CONCRETE categories (``bindings.{ro,rw}``, ``synced``) take NO root at any scope, so
  a bare-relative source there is a DEFECT and is refused where it is declared.

⚑ THE FOUR SCOPES ARE THE POINT. ``DECLARATION_ROOT_REF`` has four rows and only the AGENT
row had a live reader before 2026-08-25; a test that exercised one scope would stay green
with the other three dead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.settings.settings_launch import (
  build_launch_snapshot,
  meta_agent_path_floor,
  snapshot_category_entries,
)
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError

AGENT = "claude"
DATA = "/data"
AGENTS = "/data/agents"
WS = "/ws"
BOX = "/ws/boxes/b"


def _ctx() -> ResolveCtx:
  return ResolveCtx(
    agent_name=AGENT,
    workset_name="myws",
    host_home="/home/u",
    xdg={"XDG_DATA_HOME": DATA, "XDG_CACHE_HOME": "/xcache"},
    config={"config.data": DATA, "config.agents": AGENTS},
  )


def _floor() -> dict[str, object]:
  """The @-anchors the four DECLARATION ROOTS resolve through — nothing else."""
  floor: dict[str, object] = {
    "config.data": DATA,
    "config.agents": AGENTS,
    "meta.box.path": BOX,
    "meta.box.name": "b",
    "meta.workset.path": WS,
  }
  floor.update(meta_agent_path_floor(AGENT))
  return floor


def _sources(tmp_path: Path, **files: str) -> dict[str, str]:
  """Write the named settings files, resolve, and return ``{box_dest: host_src}``.

  *files* keys are ``system`` / ``agent`` / ``workset`` / ``box``; each value is the file's
  whole YAML text. ENV entries are dropped — every category under test is a MOUNT or a COPY.
  """
  paths: dict[str, Path | None] = {}
  for level in ("system", "agent", "workset", "box"):
    text = files.get(level)
    if text is None:
      paths[level] = None
      continue
    p = tmp_path / f"{level}.yaml"
    p.write_text(text)
    paths[level] = p
  ctx = _ctx()
  snap = build_launch_snapshot(
    agent_name=AGENT, ctx=ctx,
    system_path=paths["system"], agent_path=paths["agent"],
    workset_path=paths["workset"], box_path=paths["box"],
    default_categories=_floor(), valid_agents=(AGENT,),
  )
  return {
    e.box_dest: e.host_src
    for e in snapshot_category_entries(snap, active_agent=AGENT, box_ctx=ctx)
    if e.delivery != "ENV"
  }


# --------------------------------------------------------------------------- #
# (A) ABSTRACT categories — ROOTED AT DECLARATION, at EACH of the four scopes  #
# --------------------------------------------------------------------------- #

#: ``(level, file text, dest, expected rooted source)`` — one row per SCOPE row of the
#: spec's DECLARATION-ROOT table, each written the way a user writes it.
#: ⚑ The agent row's file root table is ``self:`` — ``self`` IS ``agent.<node>``.
_ROOTED = (
  ("system", 'system:\n  seeded: {"~/s": ["leaf"]}\n',
   "/home/agent/s", f"{DATA}/seeded/leaf"),
  ("agent", 'self:\n  caches: {"~/a": ["leaf"]}\n',
   "/home/agent/a", f"{AGENTS}/{AGENT}/caches/leaf"),
  ("workset", 'workset:\n  common: {"~/w": ["leaf"]}\n',
   "/home/agent/w", f"{WS}/common/leaf"),
  ("box", 'box:\n  caches: {"~/x": ["leaf"]}\n',
   "/home/agent/x", f"{BOX}/caches/leaf"),
)


@pytest.mark.parametrize(("level", "text", "dest", "expected"), _ROOTED)
def test_a_bare_leaf_is_rooted_at_every_declaring_scope(
  tmp_path: Path, level: str, text: str, dest: str, expected: str,
) -> None:
  """A bare LEAF in an abstract category resolves under ``<scope-root>/<category>/``.

  (Mutation: drop the ``root_ref=`` argument at either ``parse_bind_map`` call site in
  ``settings_assemble._parse_node`` → every row fails with the bare ``'leaf'``.)
  """
  assert _sources(tmp_path, **{level: text})[dest] == expected


#: The same four scopes with an ALREADY SELF-RESOLVING source. The root is a DEFAULT FOR
#: RELATIVE SOURCES, not a universal law (§2a), so each must pass through UNTOUCHED.
_VERBATIM = (
  ("system", 'system:\n  seeded: {"~/s": ["/abs/dir"]}\n', "/home/agent/s", "/abs/dir"),
  ("agent", 'self:\n  caches: {"~/a": ["$XDG_DATA_HOME/x"]}\n',
   "/home/agent/a", f"{DATA}/x"),
  ("workset", 'workset:\n  common: {"~/w": ["@meta.workset.path/own"]}\n',
   "/home/agent/w", f"{WS}/own"),
  ("box", 'box:\n  caches: {"~/x": ["~/tdir"]}\n', "/home/agent/x", "/home/u/tdir"),
)


@pytest.mark.parametrize(("level", "text", "dest", "expected"), _VERBATIM)
def test_a_self_resolving_source_is_stored_verbatim_at_every_scope(
  tmp_path: Path, level: str, text: str, dest: str, expected: str,
) -> None:
  """No scope root is joined onto a source that already resolves on its own.

  (Mutation: drop the ``is_self_resolving`` test in ``agent_config.root_relative_source``
  → every row fails with the scope root prepended to an already-absolute source.)
  """
  assert _sources(tmp_path, **{level: text})[dest] == expected


def test_the_box_root_is_the_boxs_own_path_not_the_worksets(tmp_path: Path) -> None:
  """The box and workset rows are DIFFERENT roots, so one entry each must land apart.

  A single shared root would satisfy both rows of the table with one value; this is the
  case that tells them apart.
  """
  sources = _sources(
    tmp_path,
    workset='workset:\n  common: {"~/w": ["leaf"]}\n',
    box='box:\n  common: {"~/x": ["leaf"]}\n',
  )
  assert sources["/home/agent/w"] == f"{WS}/common/leaf"
  assert sources["/home/agent/x"] == f"{BOX}/common/leaf"


def test_a_pref_is_rooted_at_the_key_it_targets(tmp_path: Path) -> None:
  """A ``pref.agent.<a>.<abstract>`` request is stored as its TARGET key would store it.

  A pref's value is installed AT the target key (§2h), so leaving it unrooted would put the
  divergence one level up instead of removing it.
  """
  sources = _sources(
    tmp_path,
    box=(
      "pref:\n"
      "  agent:\n"
      f"    {AGENT}:\n"
      '      caches: {"~/p": ["leaf"]}\n'
    ),
  )
  assert sources["/home/agent/p"] == f"{AGENTS}/{AGENT}/caches/leaf"


# --------------------------------------------------------------------------- #
# (B) CONCRETE categories — a bare-relative source is REFUSED where declared   #
# --------------------------------------------------------------------------- #

_CONCRETE = (
  ("box", 'box:\n  bindings:\n    rw: {"~/y": ["relsrc"]}\n', "bindings.rw"),
  ("box", 'box:\n  bindings:\n    ro: {"~/y": ["relsrc"]}\n', "bindings.ro"),
  ("box", 'box:\n  synced: {"~/z": ["relsrc"]}\n', "synced"),
  ("workset", 'workset:\n  synced: {"~/z": ["relsrc"]}\n', "synced"),
  ("system", 'system:\n  bindings:\n    rw: {"~/y": ["relsrc"]}\n', "bindings.rw"),
  ("agent", 'self:\n  synced: {"~/z": ["relsrc"]}\n', "synced"),
)


@pytest.mark.parametrize(("level", "text", "category"), _CONCRETE)
def test_a_relative_concrete_source_is_refused_by_name(
  tmp_path: Path, level: str, text: str, category: str,
) -> None:
  """The refusal names the CATEGORY, the DESTINATION and the offending SOURCE.

  (Mutation: return *src* instead of raising in ``settings_assemble._declared_source`` →
  every row fails, the file loading silently with a source podman would read as a named
  volume.)
  """
  with pytest.raises(SettingsError) as exc:
    _sources(tmp_path, **{level: text})
  message = str(exc.value)
  assert category in message
  assert "'relsrc'" in message
  assert "fully resolve on its own" in message


def test_a_dot_relative_concrete_source_is_refused_too(tmp_path: Path) -> None:
  """``./x`` is refused as well: podman resolves it against its OWN working directory.

  It is not a named volume — it is worse, a path that means a different thing depending on
  where the user happened to run kanibako from. Neither is a source that resolves on its own.
  """
  with pytest.raises(SettingsError):
    _sources(tmp_path, box='box:\n  synced: {"~/z": ["./x"]}\n')


@pytest.mark.parametrize("src", ["/abs/dir", "~/tdir", "$XDG_DATA_HOME/x", "@config.data/x"])
def test_a_self_resolving_concrete_source_is_accepted(tmp_path: Path, src: str) -> None:
  """The refusal bites the RELATIVE source only — every self-resolving spelling still loads."""
  sources = _sources(tmp_path, box=f'box:\n  synced: {{"~/z": ["{src}"]}}\n')
  assert sources["/home/agent/z"]
  assert not sources["/home/agent/z"].startswith(BOX + "/synced")
