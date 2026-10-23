"""Channel roots: which owner makes each one, and the launch check for a missing one.

A root is made with its owner (system-design § Delivery at launch, "Missing read-write
sources"): the system type-roots and the primary workset's roots by ``setup``
(:func:`make_setup_channel_roots`), a named workset's roots by ``workset create``
(:func:`make_workset_channel_roots`), and a box's inbox with the box
(:func:`make_box_inbox`).  A launch never recreates a root: it refuses
(:func:`missing_channel_root_error`).  The inbox is not a root; a launch creates it
when it is missing.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako import boxfs
from kanibako.channels import channels as _ch

if TYPE_CHECKING:
  from kanibako.settings.paths import ProjectPaths, StandardPaths


def _system_roots(std: StandardPaths) -> list[tuple[str, Path | None]]:
  return [
    ("system.channels.common", std.channels_common),
    ("system.channels.chat", std.channels_chat),
    ("system.channels.share", std.channels_share),
    ("system.channels.mailboxes", std.channels_mailboxes),
  ]


def _workset_roots(
  std: StandardPaths, ws_token: str, ws_root: Path, *, local: bool = True
) -> list[tuple[str, Path | None]]:
  """A workset's mailbox partition, the parent of every member's inbox, and with *local*
  its own roots (a standalone box has none)."""
  from kanibako.settings.workset_dirkeys import EarlyScope

  roots: list[tuple[str, Path | None]] = []
  wch = (
    _ch.workset_channels_at(ws_root, early=EarlyScope(std.early_system, ws_token))
    if local
    else None
  )
  if wch is not None:
    roots += [
      ("workset.channels.common", wch.common),
      ("workset.channels.chat", wch.chat),
      ("workset.channels.share", wch.share),
    ]
  roots.append(
    ("workset.channels.mailboxes", _ch.partition_key_paths(std, ws_token, ws_root).mailboxes)
  )
  return roots


def _setup_roots(std: StandardPaths) -> list[Path]:
  """The roots ``setup`` makes; the standalone partition is shared by every standalone box."""
  roots = [p for _, p in _system_roots(std) if p is not None]
  roots += [
    p for _, p in _workset_roots(std, _ch.WS_TOKEN_PRIMARY, std.primary_workset) if p is not None
  ]
  standalone = _ch.system_partition(std, _ch.WS_TOKEN_STANDALONE).mailboxes
  if standalone is not None:
    roots.append(standalone)
  return roots


def _make_root(path: Path) -> bool:
  """Make *path*; True when this call made it.

  ⚑ A partition sits in the box-writable mailboxes tree, so a leaf whose parent
  exists is made through :mod:`kanibako.boxfs`, which never follows a link there.
  """
  if path.is_dir():
    return False
  if path.parent.is_dir():
    boxfs.mkdir(path, path.parent, exist_ok=True)
  else:
    path.mkdir(parents=True, exist_ok=True)
  return True


def make_setup_channel_roots(std: StandardPaths) -> None:
  """Make the system type-roots, the primary workset's roots, and both reserved partitions."""
  for path in _setup_roots(std):
    _make_root(path)


def make_workset_channel_roots(std: StandardPaths, name: str, root: Path) -> list[Path]:
  """Make a new workset's roots; returns the ones made, for the caller's unwind.

  A root outside *root* other than the partition is the user's directory and is not
  made, as ``create_workset``'s skeleton rule has it.  The partition is made only
  under an existing mailboxes root: without one, ``setup`` has not run and the launch
  says so.
  """
  made: list[Path] = []
  for key, path in _workset_roots(std, name, root):
    if path is None:
      continue
    if key == "workset.channels.mailboxes":
      if not path.parent.is_dir():
        continue
    elif not path.is_relative_to(root):
      continue
    if _make_root(path):
      made.append(path)
  return made


def make_box_inbox(std: StandardPaths, ws_token: str, ws_root: Path, box_name: str) -> Path | None:
  """Make a new box's inbox; returns it when this call made it.

  ``None`` when the inbox exists, its key is null, or its partition is missing: the
  launch refuses a missing partition, so creating it here would hide that.  ``None``
  too when the key does not resolve or the mkdir fails: the launch then refuses or
  creates the inbox, and says so.
  """
  from kanibako.settings.settings_resolve import SettingsError

  try:
    partition = _ch.partition_key_paths(std, ws_token, ws_root).mailboxes
  except SettingsError:
    return None
  if partition is None or not partition.is_dir():
    return None
  inbox = partition / box_name
  if inbox.is_dir():
    return None
  try:
    boxfs.mkdir(inbox, partition, exist_ok=True)
  except OSError:
    return None
  return inbox


def make_proj_inbox(std: StandardPaths, proj: ProjectPaths) -> Path | None:
  """:func:`make_box_inbox` for a resolved box."""
  if proj.name is None:
    return None
  return make_box_inbox(std, _ch.workset_name_token(proj), _ch.workset_root(proj, std), proj.name)


def _box_roots(std: StandardPaths, proj: ProjectPaths | None) -> list[tuple[str, Path | None]]:
  """*proj*'s channel roots: the system's, and its workset's (none without a box)."""
  if proj is None:
    return _system_roots(std)
  return _system_roots(std) + _workset_roots(
    std,
    _ch.workset_name_token(proj),
    _ch.workset_root(proj, std),
    local=_ch.has_workset_channels(proj),
  )


def is_channel_root(std: StandardPaths, proj: ProjectPaths | None, path: Path) -> bool:
  """True when *path* is one of *proj*'s channel roots, which only their owners make."""
  return any(p == path for _, p in _box_roots(std, proj))


def missing_channel_root_error(std: StandardPaths, proj: ProjectPaths) -> str | None:
  """The launch refusal for *proj*'s missing channel roots, or ``None``.

  Each root is named with its cure: ``kanibako setup`` for one setup makes, else
  ``mkdir -p``.  A null key names no root.
  """
  roots = _box_roots(std, proj)
  missing = [(k, p) for k, p in roots if p is not None and not p.is_dir()]
  if not missing:
    return None
  setup_made = set(_setup_roots(std))
  lines = [
    f"Error: box '{proj.name or proj.project_path}' cannot launch: "
    "a channel directory is missing, and a launch does not recreate one."
  ]
  for key, path in missing:
    cure = (
      "run 'kanibako setup'"
      if path in setup_made
      else f"create it with: mkdir -p {shlex.quote(str(path))}"
    )
    lines.append(f"  {key}: {path} — {cure}")
  return "\n".join(lines)
