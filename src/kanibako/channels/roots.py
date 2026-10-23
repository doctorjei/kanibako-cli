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

import os
import shlex
import stat
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from kanibako import boxfs
from kanibako.channels import channels as _ch
from kanibako.errors import KanibakoError

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


def outermost_channel_root(
  std: StandardPaths, proj: ProjectPaths | None, path: Path
) -> Path | None:
  """The SHALLOWEST of *proj*'s channel roots at or above *path*, or ``None``.

  A channel root is box-writable, so a host file call under it takes it as the
  :mod:`kanibako.boxfs` seam root: no link a box planted below it is followed.
  ⚑ The outermost, never the deepest: a root nested in another is itself writable by
  every box with the outer root, which can swap it for a link, and the seam trusts
  its root.
  """
  covering = [p for _, p in _box_roots(std, proj) if p is not None and path.is_relative_to(p)]
  return min(covering, key=lambda p: len(p.parts), default=None)


def _set_command(proj: ProjectPaths, key: str) -> str | None:
  """The ``set`` command that writes *key* for *proj*'s scope, or ``None`` when none runs.

  ⚑ Measured spellings: the primary workset is ``default``; a standalone box's
  workset has no set door (its box tier may not write a workset key).
  """
  from kanibako.settings.paths import BoxMode

  if key.startswith("system."):
    return "kanibako system set"
  if proj.mode is BoxMode.primary:
    return "kanibako workset set default"
  if proj.mode is BoxMode.named and proj.group is not None:
    return f"kanibako workset set {shlex.quote(proj.group.name)}"
  return None


def _as_owner(path: Path, command: str) -> str:
  """*command*, run as *path*'s owner: under ``podman unshare`` when that is not us.

  A box with sudo makes files owned by a host subuid, which only the rootless
  namespace can change.  ⚑ Printed as podman for every runtime, as every other
  ``podman unshare`` cure is.
  """
  try:
    foreign = os.lstat(path).st_uid != os.getuid()
  except OSError:
    foreign = False
  return f"podman unshare {command}" if foreign else command


def _blocking_dir(path: Path, outer: Path | None) -> Path:
  """The directory at or above *path*, not above *outer*, that we cannot read or search.

  :func:`boxfs.first_symlink` returns the entry it failed to open, which is the CHILD
  of a dir that can be listed but not searched (mode 0600); that child cannot even be
  stat'ed.  *path* itself when no such dir is found.
  """
  for d in (path, *path.parents):
    if outer is not None and not d.is_relative_to(outer):
      break
    try:
      os.lstat(d)
    except OSError:
      continue
    if not os.access(d, os.R_OK | os.X_OK, follow_symlinks=False):
      return d
    if d == outer:
      break
  return path


def _link_lines(
  key: str, link: Path, outer: Path | None, remake: Path | None, user_cure: str | None
) -> list[str]:
  """The refusal lines for *key*, whose path has *link* under the channel root *outer*.

  The box cure removes *link* and, with *remake*, makes that path again; each command
  runs as the owner of the directory it changes (:func:`_as_owner`).
  ⚑ :func:`boxfs.first_symlink` fails closed: a directory it cannot open comes back as
  if a link.  The dir that blocks it (:func:`_blocking_dir`) is named as unreadable,
  with a ``chmod`` cure instead.
  """
  q = shlex.quote
  try:
    is_link = stat.S_ISLNK(os.lstat(link).st_mode)
  except OSError:
    is_link = False
  if not is_link:
    locked = _blocking_dir(link, outer)
    return [
      f"  {key}: {locked} inside {outer} cannot be read, so it cannot be checked for a link.",
      f"    If a box made it: {_as_owner(locked, f'chmod u+rwx {q(str(locked))}')}",
    ]
  box_cure = _as_owner(link.parent, f"rm {q(str(link))}")
  if remake is not None:
    box_cure += f" && {_as_owner(link.parent, f'mkdir -p {q(str(remake))}')}"
  lines = [
    f"  {key}: {link} is a link inside {outer}, which a box can write.",
    f"    If a box made it: {box_cure}",
  ]
  if user_cure is not None:
    lines.append(f"    If you made it: {user_cure}")
  return lines


def missing_channel_root_error(std: StandardPaths, proj: ProjectPaths) -> str | None:
  """The launch refusal for *proj*'s missing channel roots, or ``None``.

  Each root is named with its cure: ``kanibako setup`` for one setup makes, else
  ``mkdir -p``.  A null key names no root.
  """
  roots = _box_roots(std, proj)
  setup_made = set(_setup_roots(std))
  lines: list[str] = []
  for key, path in roots:
    if path is None:
      continue
    # ⚑ A root nested in another is checked through the seam from the outer one: a
    # link there (a box can plant one) is not a root, though ``is_dir`` follows it.
    outer = outermost_channel_root(std, proj, path)
    link = None if outer is None or outer == path else boxfs.first_symlink(path, outer)
    if link is not None:
      q = shlex.quote
      set_cmd = _set_command(proj, key)
      lines += _link_lines(
        key,
        link,
        outer,
        path,
        None if set_cmd is None else f"{set_cmd} {key}={q(str(path.resolve()))}",
      )
    elif not path.is_dir():
      cure = (
        "run 'kanibako setup'"
        if path in setup_made
        else f"create it with: mkdir -p {shlex.quote(str(path))}"
      )
      lines.append(f"  {key}: {path} — {cure}")
  if not lines:
    return None
  head = (
    f"Error: box '{proj.name or proj.project_path}' cannot launch: "
    "a channel directory is missing, a link, or unreadable, and a launch does not recreate one."
  )
  return "\n".join([head, *lines])


class ChannelLinkError(KanibakoError):
  """A bind source in a channel tree has a link a box could have planted."""


def refuse_linked_channel_sources(
  std: StandardPaths, proj: ProjectPaths | None, sources: Iterable[tuple[str, Path, bool]]
) -> None:
  """Raise :class:`ChannelLinkError` when a ``(dest, src, read_only)`` bind source has a link.

  A source under a channel root other than the root itself is box-writable down to the
  outermost root, so a link at it or between them (another box's inbox swapped for
  one) would redirect the bind.  The root itself is :func:`missing_channel_root_error`'s.
  ⚑ The box cure is a bare ``rm``: the launch makes a missing rw source, and a ro
  source may be a file, where ``mkdir -p`` would be wrong.  There is no user cure: no
  ``set`` command moves one bind entry (it is written in a settings file).
  """
  lines: list[str] = []
  for dest, src, read_only in sources:
    outer = outermost_channel_root(std, proj, src)
    link = None if outer is None or outer == src else boxfs.first_symlink(src, outer)
    if link is not None:
      key = f"box.bindings.{'ro' if read_only else 'rw'}[{dest}]"
      lines += _link_lines(key, link, outer, None, None)
  if lines:
    subject = "this box" if proj is None else f"box '{proj.name or proj.project_path}'"
    head = (
      f"{subject} cannot launch: a bind source is behind a link or an unreadable "
      "directory in a channel directory, and a launch does not follow one."
    )
    raise ChannelLinkError("\n".join([head, *lines]))
