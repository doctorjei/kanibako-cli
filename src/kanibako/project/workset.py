"""Workset data model and persistence — see ``llm-docs/kanibako/project/workset.py.md``.

A *workset* is a named group of projects whose persistent state lives under a
single root directory chosen by the user.  Terminology:

* **workset root** — the user-chosen dir; holds the boxes dir, the workspaces dir,
  ``vault/``, the logs dir, ``auth/``, ``channels/`` and — BOTH OPTIONAL —
  ``registry.yaml`` and ``workset.yaml``.  ⚑ A freshly created root has FOUR
  DIRS AND NO FILES.  ⚑ Only ``vault/`` is spelled with a literal leaf here: the
  others are REPOINTABLE keys, so their on-disk names are whatever
  ``workset.{boxes,workspaces,logs,channelroot}`` resolve to.  ⚑⚑ ``vault/`` is
  the literal only as a PARENT — its two arms ``workset.{vault_ro,vault_rw}``
  are themselves repointable keys and are RESOLVED, so a box's vault need not
  live under this root at all.
* **identity** — the workset's entry in the GLOBAL registry's ``worksets:``
  section (``{config.registry}``), mapping its NAME to its ROOT.  ⚑⚑ THAT MAPPING
  IS THE WHOLE OF A WORKSET'S IDENTITY: nothing under the workset root records a
  name, and there is no identity table in either file there.  ⚑ That is a fact
  about NAMING, not about FINDING (system-design § "Detection & import"): a root
  is still found on disk by its four-dir skeleton (:func:`is_workset_skeleton`),
  and an unregistered one is imported under its leaf directory name — exactly as
  ``workset create`` already defaults a name it was not given.  The
  root ``registry.yaml`` carries the ``boxes:`` MEMBERSHIP only; the root
  ``workset.yaml`` carries SETTINGS ONLY, is sparse, and MAY BE ABSENT.  The
  runtime ``meta.workset.*`` keys (spec §1A) are derived at launch from the
  treewalk, never read off disk.  ⚑ 1.6.0/1.7.x kept a ``workset.meta`` identity
  table in ``workset.yaml``, and an unreleased 1.8.0 build a ``meta.workset``
  one; BOTH are RETIRED and now HARD-REFUSE — see
  :func:`refuse_retired_workset_identity`.
* **default workset** — the synthesized, never-persisted group of default-mode
  projects, rooted at ``{config.primary_workset}``.
* **connected (external) box** — a member whose registered path lies OUTSIDE the
  workset root (D10).

⚑ The llm-doc carries the CORRECTED root layout; the tree that used to live here
had drifted (it named a ``worksets.yaml`` that no longer exists).
"""

from __future__ import annotations

import copy
import shlex
import sys
from collections.abc import Collection, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any, Callable, NamedTuple

from kanibako.project import registry_store, workset_registry
from kanibako.settings import bootstrap
from kanibako.settings.config_io import load_doc
from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.errors import (
  ConfigError,
  LegacyWorksetIdentityError,
  ProjectError,
  ReservedWorksetNameError,
  WorksetError,
)
from kanibako.identifiers import find_identifier, name_flaw
from kanibako.project.names import read_names, register_name, unregister_name
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.messages import (
  ERR_CONFIG_NULL_PATH,
  ERR_LOOP_WORKSPACE_BIND,
  ERR_NULL_WORKSPACE_BIND,
  ERR_STANDALONE_NULL_WORKSPACES,
  ERR_WORKSET_NULL_WORKSPACES,
  ERR_WORKSET_NAME,
  NAME_CHARSET,
)
from kanibako.settings.settings_resolve import UNSET, SettingsError
from kanibako.settings.workset_dirkeys import (
  EarlyScope,
  EarlySystem,
  _stored_repoint,
  early_repoint,
  refuse_inherited_per_owner,
  resolve_workset_dir_key,
)
from kanibako.utils import (
  WORKSET_SEGMENT_PRIMARY,
  WORKSET_SEGMENT_STANDALONE,
  is_dir_unless_gone,
  is_file_unless_gone,
  literal_path,
  refuse_unreadable_file,
)

# ⚑ FORWARD edge of a documented cycle: ``settings/paths.py`` breaks it by DEFERRING
# its ``project.workset`` imports into function bodies — do not add a module-scope
# edge back this way.
from kanibako.settings.paths import (
  StandardPaths,
  _workset_box_paths,
  box_log_files,
  box_logs_to_remove,
)

# ⚑⚑ EVERY NAME BELOW IS AN ALIAS, NEVER A VALUE.  The defaults themselves live in
# ``settings/bootstrap.py``, the designated path-literal file, and are materialized
# THERE AND NOWHERE ELSE; re-spelling one here made a second carrier that could drift.
# The local names stay only because the call sites read better with them.

# Default leaves for the RESOLVED workset dir keys — the spec's per-mode default
# formula ``{meta.workset.path}/<leaf>``, applied ONCE per key in its resolver below
# (§3.3: real and USED).  ⚑ A default leaf is what a key falls back to, NOT the path
# component: every one of these is repointable, so nothing may join it directly.
BOXES_DIR_NAME = bootstrap.BOXES_PATH
_STANDALONE_BOXES_LEAF = bootstrap.STANDALONE_META_DIR
_WORKSPACES_LEAF = bootstrap.WORKSPACES_PATH
_STANDALONE_WORKSPACE_LEAF = bootstrap.WORKSPACE_PATH
_CHANNELROOT_LEAF = bootstrap.CHANNELS_PATH
_LOGS_LEAF = bootstrap.LOGS_PATH

# The two leaves the WORKSET STAMP writes (``launch/templates.py``).  ⚑ They are the
# WORKSET-scope spelling of two entries ``templates.SCOPE_WHITELISTS["workset"]``
# permits; ``templates.AGENT_TEMPLATE_STORE_REL`` is the AGENT-scope carrier of the
# same word and stays separate — an agent store's ``template/`` is a fixed store leaf,
# a workset's is the repointable ``workset.template``, and importing one for the other
# would invert the project -> launch dependency as well as conflate two keys.
# ⚑ They are NOT in ``bootstrap`` with their five siblings because no OTHER module
# spells them; add them there the moment a second consumer appears.
_CANON_LEAF = "canon"
_TEMPLATE_LEAF = "template"

# The ref the STANDALONE ``workset.logs`` default is (spec §2c: ``workset.logs |
# {workset.boxes}``).  ⚑ A REF NAME, not a leaf: nothing here may join it as a path component.
_BOXES_REF = f"workset.{BOXES_DIR_NAME}"

# ⚑ The ONE skeleton dir that names NO KEY: the keyspec declares ``workset.vault_ro``
# and ``workset.vault_rw`` (``{meta.workset.path}/vault/ro``, ``.../vault/rw``) and no
# ``workset.vault`` at all, so ``vault/`` is only their shared DEFAULT PARENT — there is
# nothing to resolve it through and it is always ``<root>/vault``.  ⚑ DELIBERATE: it keeps
# the skeleton a SINGLE list that ``create_workset`` stamps and ``is_workset_skeleton``
# tests.
# ⚑⚑ THE PARENT IS THE NON-KEY; THE TWO ARMS ARE NOT.  ``vault_ro`` and ``vault_rw`` are
# declared, CLI-settable, repointable keys, so the arms are RESOLVED below and nothing may
# compose ``_VAULT_LEAF / ro`` again, or a repoint is ignored by the filesystem.
_VAULT_LEAF = bootstrap.VAULT_PATH
_VAULT_RO_KEY = "vault_ro"
_VAULT_RW_KEY = "vault_rw"
_VAULT_RO_LEAF = f"{_VAULT_LEAF}/{bootstrap.RO_PATH}"
_VAULT_RW_LEAF = f"{_VAULT_LEAF}/{bootstrap.RW_PATH}"


# ---------------------------------------------------------------------------
# Resolved workset dir keys (workset.workspaces / workset.channelroot / workset.canon
# / workset.template / workset.vault_ro / workset.vault_rw) — thin per-key faces over
# the ONE no-snapshot route,
# ``settings/workset_dirkeys.resolve_workset_dir_key``.  ⚑ These read the leaf out
# of the workset.yaml table; the ROUTE owns every token rule ({key}-refs, {$XDG_*}, ~) and
# owns the refusal.  Do not give any of them a private expansion again.
# ---------------------------------------------------------------------------


def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None:
  """Read *root*'s workset ``workset.yaml``: ``None`` when ABSENT, refused when malformed.

  ⚑ READ LIKE EVERY OTHER SETTINGS FILE (``load_doc``), so a malformed one is refused
  in its OWN words, naming the file.  ⚑ CARVE-OUTS CATCH ``ConfigError``:
  the ancestor walk (which still refuses an UNREADABLE file), and
  :func:`delete_workset`'s purge.
  """
  path = root / WORKSET_META_FILE
  # ⚑ "Absent" only when it IS absent: an unreadable path or a loop refuses, naming it.
  if not is_file_unless_gone(path):
    return None
  return load_doc(path)


def _resolve_workset_dir(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  leaf: str,
  key: str,
  *,
  early: EarlyScope,
  standalone: bool | None = None,
) -> Path | None:
  """Return the resolved ``workset.<key>`` dir (default ``<root>/<leaf>``).

  ``None`` for a present ``<None>``.  *standalone* is passed to
  :func:`resolve_workset_dir_key` as given.
  """
  repoint, where = early_repoint(workset_root, workset_settings, key, early=early)
  if repoint is None:
    return None
  return resolve_workset_dir_key(
    workset_root,
    repoint if isinstance(repoint, str) else None,
    leaf,
    key=key,
    where=where,
    standalone=standalone,
    workset_settings=workset_settings,
    early=early,
  )


def resolve_workset_workspaces(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  standalone: bool = False,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.workspaces`` dir (*standalone* selects the singular default).

  ``None`` when ``workset.workspaces`` is a present ``<None>``
  (keyspec §2h "the ordinary present-`None` rule"): there is no workspaces dir, and the default
  leaf would be a path the user said does not exist.
  """
  leaf = _STANDALONE_WORKSPACE_LEAF if standalone else _WORKSPACES_LEAF
  return _resolve_workset_dir(
    workset_root, workset_settings, leaf, _WORKSPACES_LEAF, early=early, standalone=standalone
  )


def resolve_workspaces_locator(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  early: EarlyScope,
) -> Path:
  """Where a named root's in-tree members are FOUND: :func:`resolve_workset_workspaces`,
  or its default leaf under a null — for DETECTION only, never a place to create in.

  Members made before a null still sit at the default, and a lookup must still find them.
  """
  workspaces = resolve_workset_workspaces(workset_root, workset_settings, early=early)
  if workspaces is not None:
    return workspaces
  return resolve_workset_dir_key(
    workset_root,
    None,
    _WORKSPACES_LEAF,
    key=_WORKSPACES_LEAF,
    early=early,
  )


def _workspaces_null_file(
  workset_root: Path,
  *,
  early: EarlyScope,
) -> Path | None:
  """The settings file whose present ``<None>`` nulls *workset_root*'s ``workset.workspaces``."""
  repoint, where = early_repoint(
    workset_root,
    load_workset_settings_doc(workset_root),
    _WORKSPACES_LEAF,
    early=early,
  )
  return where if repoint is None else None


def workset_workspaces_nulled(workset_root: Path, *, early: EarlyScope) -> bool:
  """True when *workset_root*'s ``workset.workspaces`` is a present ``<None>`` — no workspace dir.

  ⚑ Read off the root's workset.yaml and the system file beneath it, like every face here.
  The PRIMARY workset's <None> is the launch floor's (spec §2c), not a file value, so a
  primary root answers from its files.
  """
  return _workspaces_null_file(workset_root, early=early) is not None


def refuse_null_workspaces(
  workset_root: Path,
  what: str,
  *,
  standalone: bool = False,
  early: EarlyScope,
) -> None:
  """RAISE, naming ``workset.workspaces`` and the file, when *workset_root* nulls it
  (keyspec §2h "the ordinary present-`None` rule", Q96).

  For every operation that would CREATE or COPY a workspace under the root: a null means the
  user said there is no workspace dir, and taking the default instead is the defect.  *what*
  completes "cannot hold …" (e.g. ``"a new workspace for 'app'"``).  *standalone* selects
  the lone-box cure: a standalone root has no outside member to connect instead.
  """
  null_file = _workspaces_null_file(workset_root, early=early)
  if null_file is not None:
    message = ERR_STANDALONE_NULL_WORKSPACES if standalone else ERR_WORKSET_NULL_WORKSPACES
    raise WorksetError(message % (null_file, what))


def refuse_null_box_workspace(
  workset_root: Path,
  workspace: Path | None,
  box: str,
  *,
  standalone: bool,
  early: EarlyScope,
) -> None:
  """RAISE when a box's ``meta.box.workspace`` resolves through a null ``workset.workspaces`` (Q106).

  The workspace bind is mounted at every launch (system-design § "The workspace bind"), so a
  box with no workspace cannot run.  Standalone's workspace IS ``{workset.workspaces}``; a
  named member's is ``{workset.workspaces}/<name>`` unless it is EXTERNAL — its recorded
  *workspace* lies outside *workset_root* and resolves through no key, so it still launches.
  Primary is not asked: its workspace is the project dir.  ⚑ A LOOP REFUSES: True means
  raise here, and :func:`_path_in_tree` answers a loop False.
  """
  null_file = _workspaces_null_file(workset_root, early=early)
  if null_file is None:
    return
  if workspace is not None and _symlink_loop(workspace):
    raise WorksetError(ERR_LOOP_WORKSPACE_BIND % (box, workspace))
  if standalone or workspace is None or _path_in_tree(workspace, workset_root):
    raise WorksetError(ERR_NULL_WORKSPACE_BIND % (box, null_file))


def resolve_workset_boxes(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  standalone: bool = False,
  early: EarlyScope,
) -> Path:
  """Return the resolved ``workset.boxes`` dir (*standalone* selects the ``box_data`` default).

  ⚑ THE FLAG SELECTS A DEFAULT LEAF, NOTHING ELSE — the same shape as
  ``resolve_workset_workspaces``: spec §2c gives standalone
  ``{meta.workset.path}/box_data`` where primary/named get ``.../boxes``.
  ⚑ DETECTION does NOT compose ``box_data/``: a standalone root is the one whose own
  ``registry.yaml`` holds the standalone mark.  This key's DEFAULT LEAF marks nothing.

  🛑 A present ``<None>`` REFUSES, naming the key and the file
  (keyspec §2h "the ordinary present-`None` rule", Q96): every box's home and settings
  live under this dir, so there is no box without it, and taking the default instead
  would put boxes where the user said there is no store.
  """
  default_leaf = _STANDALONE_BOXES_LEAF if standalone else BOXES_DIR_NAME
  repoint, where = early_repoint(workset_root, workset_settings, BOXES_DIR_NAME, early=early)
  if repoint is None:
    # ⚑ The config/system path keys' own null refusal text (``config._refuse_null_paths``).
    raise SettingsError(ERR_CONFIG_NULL_PATH % (where, f"workset.{BOXES_DIR_NAME}"))
  return resolve_workset_dir_key(
    workset_root,
    repoint if isinstance(repoint, str) else None,
    default_leaf,
    key=BOXES_DIR_NAME,
    where=where,
    standalone=standalone,
    workset_settings=workset_settings,
    early=early,
  )


def resolve_workset_logs(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  standalone: bool = False,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.logs`` dir (*standalone* takes the box-anchored default).

  ``None`` when ``workset.logs`` is a present ``<None>``: there is no logs dir, so nothing
  writes a box log and the helper-log bind is omitted (companion, "The helper-log bind").

  ⚑ STANDALONE's declared default is ``{workset.boxes}`` (spec §2c): a lone box's root IS
  the boxes dir, with no name leaf.  It is a same-set ref, so the route resolves it like
  a set value, through the same ``workset.boxes`` face answer.
  """
  repoint, where = early_repoint(workset_root, workset_settings, _LOGS_LEAF, early=early)
  if repoint is None:
    return None
  if not isinstance(repoint, str):
    repoint = f"{{{_BOXES_REF}}}" if standalone else None
  return resolve_workset_dir_key(
    workset_root,
    repoint,
    _LOGS_LEAF,
    key=_LOGS_LEAF,
    where=where,
    standalone=standalone,
    workset_settings=workset_settings,
    early=early,
  )


def resolve_workset_channelroot(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.channelroot`` — ⚑ primary/named ONLY; callers gate on mode.

  ``None`` for a present ``<None>``: no channel root, so no channel bind.
  """
  return _resolve_workset_dir(
    workset_root, workset_settings, _CHANNELROOT_LEAF, "channelroot", early=early, standalone=False
  )


def resolve_workset_canon(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.canon`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included.

  ``None`` for a present ``<None>``: the canon layer is SKIPPED (spec §2a).
  """
  return _resolve_workset_dir(
    workset_root, workset_settings, _CANON_LEAF, _CANON_LEAF, early=early
  )


def resolve_workset_template(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.template`` dir — ⚑ primary/named ONLY; <None> in standalone.

  ``None`` for a present ``<None>``, and in STANDALONE (spec §2c): no template layer.
  """
  return _resolve_workset_dir(
    workset_root, workset_settings, _TEMPLATE_LEAF, _TEMPLATE_LEAF, early=early, standalone=False
  )


def resolve_workset_vault_ro(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.vault_ro`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included.

  ``None`` for a present ``<None>``: no RO arm, so the bind is omitted (MIGRATION "A
  `null` setting inside a bind's source leaves the bind out…") and
  ``StandardPaths.primary_vault_ro`` is ``None``.
  """
  return _resolve_workset_dir(
    workset_root, workset_settings, _VAULT_RO_LEAF, _VAULT_RO_KEY, early=early
  )


def resolve_workset_vault_rw(
  workset_root: Path,
  workset_settings: Mapping[str, Any] | None,
  *,
  early: EarlyScope,
) -> Path | None:
  """Return the resolved ``workset.vault_rw`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included.

  ``None`` for a present ``<None>``, on the terms of :func:`resolve_workset_vault_ro` —
  the two arms resolve INDEPENDENTLY, so either may carry it alone.
  """
  return _resolve_workset_dir(
    workset_root, workset_settings, _VAULT_RW_LEAF, _VAULT_RW_KEY, early=early
  )


def resolve_workset_vault_pair(
  workset_root: Path,
  *,
  early: EarlyScope,
  settings_doc: Mapping[str, Any] | None = None,
) -> tuple[Path | None, Path | None]:
  """The resolved ``(vault_ro, vault_rw)`` for *workset_root*, off ONE workset.yaml read.

  ⚑ The pair form exists because EVERY consumer wants both arms, and reading the file
  once per arm opens a window for the two to disagree about the same document — the
  same reason ``_workset_skeleton_dirs`` takes one read for its three resolutions.
  ⚑ Either arm may be ``None``: a workset may null one arm and not the other.
  ⚑ *settings_doc* extends the one-read rule across consumers: a caller holding the doc
  passes it here, so arms and guard cannot answer two versions of one file.
  """
  if settings_doc is None:
    settings_doc = load_workset_settings_doc(workset_root)
  return (
    resolve_workset_vault_ro(workset_root, settings_doc, early=early),
    resolve_workset_vault_rw(workset_root, settings_doc, early=early),
  )


def _is_role_folder(entry: Path, root: Path, leaf: str) -> bool:
  """True when *entry* IS the folder ``create`` stamps at *leaf* under *root* — judged
  where the entry SITS (parent resolved, leaf not followed), so a link AT the default is
  still the default.
  """
  return _unfollowed(entry) == root.resolve() / leaf


def _users_own_repoint(
  root: Path,
  resolved: Path,
  doc: "Mapping[str, Any] | None",
  key: str,
  leaf: str,
  *,
  early: EarlyScope,
) -> bool:
  """True when the box's OWN ``workset.yaml`` points ``workset.<key>`` at a folder that
  is NOT the default *leaf* — one kanibako never created for the role.  UNSET or a
  SYSTEM-tier value is not that nomination: kanibako placed that folder itself, so the
  containment rule stands.
  """
  from kanibako.settings.workset_dirkeys import UNSET

  value, where = early_repoint(root, doc, key, early=early)
  if value is UNSET or value is None:
    return False
  return where == root / WORKSET_META_FILE and not _is_role_folder(resolved, root, leaf)


#: The role folders a box's own ``workset.yaml`` can point elsewhere, as
#: ``(key, default leaf, resolver)`` — the table a keep reads to say WHO asked for it.
_REPOINTED_ROLES = (
  (_VAULT_RO_KEY, _VAULT_RO_LEAF, resolve_workset_vault_ro),
  (_VAULT_RW_KEY, _VAULT_RW_LEAF, resolve_workset_vault_rw),
  (_CANON_LEAF, _CANON_LEAF, resolve_workset_canon),
)


def declared_repoint_key(root: Path, path: Path, *, early: EarlyScope) -> str | None:
  """The role KEY whose declaration in THIS box's own ``workset.yaml`` names *path*.

  ⚑ A keep has to say who asked, and neither old phrase can: a repointed arm IS this
  box's arm, and one inside the tree is not "outside" either.  The key names the file.
  """
  doc = load_workset_settings_doc(root)
  for key, leaf, resolve in _REPOINTED_ROLES:
    resolved = resolve(root, doc, early=early)
    if resolved is None or _unfollowed(resolved) != path:
      continue
    if _users_own_repoint(root, resolved, doc, key, leaf, early=early):
      return key
  return None


def standalone_vault_teardown(
  root: Path,
  *,
  early: EarlyScope,
) -> tuple[list[Path], list[Path]]:
  """Split a STANDALONE box's vault into ``(removable, retained)`` for a teardown.

  ⚑⚑ A standalone box's vault IS the resolved arm — there is NO per-box leaf under it
  (``settings/paths.py::_standalone_box_paths``).  So "delete this box's vault" and
  "delete the directory ``workset.vault_ro`` names" are THE SAME ACT here, and they
  are NOT the same act in named or primary mode, where only a ``<box-name>`` leaf is
  ever removed and the arm outlives every box on it.  That difference is why this
  returns a SPLIT and not a list:

  * an arm at its DEFAULT leaf (``vault/ro`` / ``vault/rw``) is kanibako's own
    skeleton, inside the tree the teardown is already clearing — it goes with the box;
  * an arm the box's OWN ``workset.yaml`` REPOINTED — inside *root* or out — is the
    USER'S FOLDER.  ⚑ The old rule read "strictly below *root* … goes with the box,
    repointed or not", and that is the data loss.  ⚑ Callers must PRINT the retained
    paths — the defect this replaced was not that the vault survived, that it survived
    SILENTLY.

  The literal ``vault/`` skeleton parent is appended to *removable* when it is a link
  (removed alone) or holds nothing but removable arms and its ``.gitignore``, so the default
  layout (which is what every pre-repoint box has) is cleared exactly as it was
  before.  ⚑ Anything else in it — the contents of an arm the user has since set to
  ``null`` included — is the user's: the skeleton stays, and each such entry lands
  in *retained* (:func:`retained_vault_reason` says why it stays).

  🛑 CALL THIS BEFORE UNLINKING THE ROOT ``workset.yaml``.  That file is the standalone
  workset tier and the only carrier of the repoint; resolving after it is gone answers
  the composed default and walks straight back into the bug.
  """
  removable: list[Path] = []
  retained: list[Path] = []
  doc = load_workset_settings_doc(root)
  # ⚑ The arms come from THE ONE pair resolver, fed the SAME doc the guards below read.
  for arm, key, leaf in zip(
    resolve_workset_vault_pair(root, early=early, settings_doc=doc),
    (_VAULT_RO_KEY, _VAULT_RW_KEY),
    (_VAULT_RO_LEAF, _VAULT_RW_LEAF),
  ):
    # ⚑ A NULL ARM IS NO SUCH DIR: it names nothing to remove and nothing to keep.
    if arm is None:
      continue
    # ⚑ A LOOP IS NOT REMOVABLE: unresolvable, so it is named, not deleted.
    # Checked FIRST because every test below resolves.
    if _symlink_loop(arm):
      retained.append(_unfollowed(arm))
    # ⚑ ROLE IDENTITY, NOT LOCATION: a box-declared non-default arm is the user's.
    elif _users_own_repoint(root, arm, doc, key, leaf, early=early):
      retained.append(_unfollowed(arm))
    elif _strictly_in_tree(arm, root):
      removable.append(_unfollowed(arm))
    else:
      retained.append(_unfollowed(arm))
  skeleton = root / _VAULT_LEAF
  if _symlink_loop(skeleton):
    retained = [p for p in retained if skeleton not in p.parents] + [skeleton]
  elif skeleton.is_symlink():
    removable.append(skeleton)
  elif skeleton.is_dir():
    arms = {arm.resolve() for arm in removable}
    leftover = [
      child
      for child in sorted(skeleton.iterdir())
      if child.name != bootstrap.IGNORE_FILE and not _holds_only_arms(child, arms)
    ]
    if leftover:
      retained.extend(leftover)
    else:
      removable.append(skeleton)
  # ⚑ A path can reach *retained* twice (an unresolvable arm is also a leftover in the
  # skeleton it sits in); name each kept path ONCE.
  return removable, list(dict.fromkeys(retained))


def standalone_canon_teardown(
  root: Path,
  *,
  early: EarlyScope,
) -> tuple[Path | None, Path | None]:
  """A STANDALONE box's ``workset.canon`` tier as ``(removable, retained)`` for a teardown.

  The tier ``create`` stamps is the box's own.  Only the tier at the DEFAULT leaf
  (``canon/``) is removable; one the box's OWN ``workset.yaml`` repoints elsewhere is
  the user's directory wherever it sits — ``canon: '<root>/src'`` is the user's source
  tree, not kanibako's handbook.  A null key retains the literal ``canon/``, as a
  nulled vault arm does.  A tier not on disk answers ``(None, None)``.  🛑 CALL THIS
  BEFORE UNLINKING THE ROOT ``workset.yaml``.
  """
  doc = load_workset_settings_doc(root)
  canon = resolve_workset_canon(root, doc, early=early)
  if canon is None:
    literal = root / _CANON_LEAF
    return None, (_unfollowed(literal) if literal.exists() or literal.is_symlink() else None)
  if not (canon.exists() or canon.is_symlink()):
    return None, None
  # ⚑ The canon key's NAME is its default leaf, so key and leaf are the same string.
  if _users_own_repoint(root, canon, doc, _CANON_LEAF, _CANON_LEAF, early=early):
    return None, _unfollowed(canon)
  if _strictly_in_tree(canon, root):
    return _unfollowed(canon), None
  return None, _unfollowed(canon)


def _sha256_file(path: Path) -> str:
  """The file's content digest; ``OSError`` propagates to the caller's fail-closed arm."""
  import hashlib

  h = hashlib.sha256()
  with path.open("rb") as fh:
    for chunk in iter(lambda: fh.read(65536), b""):
      h.update(chunk)
  return h.hexdigest()


def _create_canon_stamp(std: StandardPaths) -> dict[str, tuple[bool, "str | None"]]:
  """What ``create`` installs under the default ``canon/``: {rel path: (is_dir, sha256|None)}.

  ⚑ DERIVED from the workset MOLD's canon half -- the tree ``install_workset_template``
  copies -- never a restated list.  ⚑⚑ NOT ``canon_skeleton_rels``: that is the BOX-HOME
  skeleton (0-byte mountpoints, ``SYS_WORKSET`` under ``handbook/workset/``) and never
  runs on a root, so building the stamp from it matched a layout no ``create`` makes.
  ⚑ BYTES, not emptiness: one byte off the shipped template makes it the user's.
  ⚑ A mold file that cannot be READ stamps ``(False, None)``: kanibako cannot say what
  it ships, so the audit answers UNREADABLE and the folder is kept, never a traceback.
  ⚑⚑ THE LIVE MOLD, NOT THE PACKAGED ONE: ``create`` copies ``std.template/workset/canon``
  (:func:`workset_mold_canon_source`), which ``install_packaged_templates`` seeds once and
  an operator may edit afterwards.  Reading the copy inside the distribution instead would
  call a box's perfectly-stamped folder "content kanibako did not put there" the moment
  the live mold diverges — a false Note about bytes kanibako itself wrote.
  """
  from kanibako.launch.templates import is_copyable_file, workset_mold_canon_source
  from kanibako.settings.core_defaults import HANDBOOK_REL

  src = workset_mold_canon_source(std)
  if not src.is_dir():
    return {}
  stamp: dict[str, tuple[bool, "str | None"]] = {}
  for entry in src.rglob("*"):
    rel = entry.relative_to(src).as_posix()
    if entry.is_symlink():
      continue
    if entry.is_dir():
      stamp[rel] = (True, None)
    # ⚑ THE COPIER'S OWN FILTER (see ``is_copyable_file``), and KIND BEFORE OPEN: a
    # FIFO in the mold is neither copied nor stamped, so it is never opened here.
    elif is_copyable_file(entry):
      try:
        stamp[rel] = (False, _sha256_file(entry))
      except OSError:
        stamp[rel] = (False, None)
  # ``install_workset_template`` guarantee-creates the chapter dir even from an EMPTY
  # mold, so it is part of the stamp whether or not the mold ships anything under it.
  stamp.setdefault(HANDBOOK_REL, (True, None))
  return stamp


def _stamp_audit(
  leaf: Path, stamp: dict[str, tuple[bool, "str | None"]]
) -> tuple[bool, bool, bool]:
  """Walk *leaf* once against *stamp*: ``(every_path_shipped, every_byte_matches, checkable)``.

  ⚑ The three questions are INDEPENDENT and the Note has to be able to ask them apart.

  ``every_path_shipped`` goes False on an entry the stamp does not name, a symlink, or a
  KIND the copier would never have copied — a FIFO, a socket, a device, a dir where a file
  belongs.  ⚑⚑ The kind is read by ``lstat`` BEFORE any open: opening a FIFO to hash it
  BLOCKS on a reader that never arrives, which is how one FIFO at a stamp path turned
  ``box purge`` into a timeout instead of a kept folder.

  ``every_byte_matches`` goes False on a stamp file whose digest differs — that says ONLY
  that it differs, not who made it differ: a template changed under the box, or the box
  wrote into the file.

  ``checkable`` goes False when a file — the box's or the mold's — could not be read at
  all.  ⚑ Unreadable is NOT "differs" — kanibako knows nothing about those bytes — so it
  must not borrow the drift Note, and it must not raise: the caller has a folder to keep
  and a user to tell.
  """
  from kanibako.launch.templates import is_copyable_file

  every_path_shipped = True
  every_byte_matches = True
  checkable = True
  for p in leaf.rglob("*"):
    want = stamp.get(p.relative_to(leaf).as_posix())
    if want is None or p.is_symlink() or p.is_dir() != want[0]:
      every_path_shipped = False
      continue
    if want[0]:
      continue
    if not is_copyable_file(p):
      every_path_shipped = False
      continue
    if want[1] is None:
      checkable = False
      continue
    try:
      if _sha256_file(p) != want[1]:
        every_byte_matches = False
    except OSError:
      checkable = False
  return every_path_shipped, every_byte_matches, checkable


def holds_only_create_stamp(leaf: Path, std: StandardPaths) -> bool:
  """True when EVERY entry under *leaf* is the stamp ``create`` left, byte-for-byte.

  ⚑ This is what "kanibako's own leftover" MEANS.  A file outside the stamp, a stamp file
  the box has written into (any byte), a symlink, a non-regular file, or a file that
  cannot be read all make it the USER'S folder.
  """
  stamp = _create_canon_stamp(std)
  if not stamp:
    # ⚑ An empty stamp validates NOTHING: a leftover we cannot attribute is the user's.
    return False
  shipped, matching, checkable = _stamp_audit(leaf, stamp)
  return shipped and matching and checkable


#: A kept leftover default ``canon/`` holding what the current template does not.
#: ⚑ NO AUTHORSHIP CLAIM — mold-deleted, never-shipped and user-added files look identical.
DEFAULT_CANON_LEFTOVER_WHY = (
  "it is the default canon folder `create` made, and it holds files the current template does not"
)

#: Every path is one ``create`` installed, but the bytes are not what the mold ships now.
DEFAULT_CANON_DRIFT_WHY = (
  "it is the default canon folder `create` made, and it differs from the current template"
)

#: No template installed, so neither "does not hold" nor "differs" is knowable.
DEFAULT_CANON_NO_TEMPLATE_WHY = (
  "it is the default canon folder `create` made, and "
  "kanibako cannot check it against a template because none "
  "is installed"
)

#: Template present, but a file cannot be read — kanibako has not seen those bytes.
DEFAULT_CANON_UNREADABLE_WHY = (
  "it is the default canon folder `create` made, and "
  "kanibako cannot check it against a template because a file "
  "in it cannot be read"
)

#: Both purge doors print this, shared so two doors cannot tell one folder two stories.
DEFAULT_CANON_REMOVED_LINE = "Removed the default canon folder `create` made"


def default_canon_leftover_why(leaf: Path, std: StandardPaths) -> str:
  """The true reason a kept leftover default ``canon/`` is being kept.

  ⚑ Only ever spoken about a folder that was NOT removable, and chosen from the SAME audit
  that made that decision rather than picked by the caller.  ⚑ The order is the order of
  confidence: what kanibako can SEE beats what it can only INFER.
  """
  stamp = _create_canon_stamp(std)
  if not stamp:
    return DEFAULT_CANON_NO_TEMPLATE_WHY
  shipped, _matching, checkable = _stamp_audit(leaf, stamp)
  if not shipped:
    # ⚑ A loop INSIDE the tier is what the user must fix: name the loop's own path.
    inner = next((p for p in leaf.rglob("*") if _symlink_loop(p)), None)
    if inner is not None:
      return CANON_HOLDS_LOOP_WHY % inner
    return DEFAULT_CANON_LEFTOVER_WHY
  if not checkable:
    return DEFAULT_CANON_UNREADABLE_WHY
  return DEFAULT_CANON_DRIFT_WHY


def leftover_default_canon(
  root: Path, removable: Path | None, retained: Path | None, std: StandardPaths
) -> tuple[Path | None, Path | None, "str | None"]:
  """The default ``canon/`` a ``create`` stamped, LEFT OVER by a repoint, as
  ``(removable, retained, why_kept)``.

  ⚑ A box that repoints ``workset.canon`` leaves the default directory behind with no
  Note on it.  ⚑ "Left over" means it holds NOTHING BUT the stamp ``create`` installed
  (:func:`holds_only_create_stamp`) — a fresh one is never literally empty, it ships a
  ``handbook/``.  Anything else in it is the user's: NAMED, never taken.
  ``None, None, None`` when the default IS the tier being acted on, so nothing is handled
  twice.  ⚑ The WHY rides back with the decision: both doors report after the run, and by
  then the mold they would re-read is the same one, but the folder may not be — so the
  reason is computed here, once, from the audit that decided it.
  """
  leaf = root / _CANON_LEAF
  if removable is not None:
    return None, None, None
  if retained is not None and _unfollowed(retained) == _unfollowed(leaf):
    return None, None, None
  if not leaf.is_dir() or leaf.is_symlink():
    return None, None, None
  if holds_only_create_stamp(leaf, std):
    return _unfollowed(leaf), None, None
  return None, _unfollowed(leaf), default_canon_leftover_why(leaf, std)


def remove_default_canon_stamp(leaf: Path, std: StandardPaths) -> bool:
  """Take a leftover default ``canon/`` BOTTOM-UP: each stamp file by name, each directory
  only while empty; True only when the whole thing came down.

  ⚑ The ONLY verb that removes this path, and never a tree delete: every file is removed
  BY THE STAMP PATH and every directory is empty at the instant it goes, so content that
  appears mid-walk — or a non-stamp file — stops it and leaves the rest untouched.
  ⚑ The stamp lands 555/444 (J-7) and ``unlink`` needs WRITE ON THE PARENT, so a stamp
  directory is re-opened for its owner before its files are taken.  Only stamp-owned paths
  are chmod-ed — user content is neither written nor widened.
  ⚑ The stamp is re-read from the LIVE mold HERE, at the delete, not carried from the plan:
  the mold is the authority on what may go, and a folder that cannot be matched to it now
  does not go.
  """
  import os
  import stat

  from kanibako.launch.templates import is_copyable_file

  stamp = _create_canon_stamp(std)
  if not stamp:
    # ⚑ Same rule as the planner: an empty stamp validates NOTHING, so an empty leaf
    # would otherwise come away with no mold to say kanibako made it.
    return False
  # ⚑ The leaf's OWN stamp files (COLLECTION.md sits directly in it) are taken by the
  # same walk; ``rglob`` never yields the root, so append it last.
  dirs = sorted(
    (d for d in leaf.rglob("*") if d.is_dir() and not d.is_symlink()),
    key=lambda d: len(d.parts),
    reverse=True,
  )
  dirs.append(leaf)
  for d in dirs:
    if d != leaf:
      want_dir = stamp.get(d.relative_to(leaf).as_posix())
      if want_dir is None or want_dir[0] is not True:
        return False
    if not os.access(d, os.W_OK):
      try:
        d.chmod(stat.S_IMODE(os.lstat(d).st_mode) | stat.S_IWUSR)
      except OSError:
        return False
    for f in sorted(d.iterdir()):
      rel = f.relative_to(leaf).as_posix()
      want = stamp.get(rel)
      if f.is_symlink() or want is None or want[0] is not False:
        return False
      # ⚑ KIND BEFORE OPEN: a FIFO swapped in here would block this open forever.
      if not is_copyable_file(f):
        return False
      # ⚑ The planner's BYTE rule, RE-APPLIED at delete time: bytes can arrive
      # between the two, and a written-into stamp file is the user's.
      if want[1] is None:
        return False
      try:
        if _sha256_file(f) != want[1]:
          return False
      except OSError:
        return False
      try:
        f.unlink()
      except OSError:
        return False
    # ⚑ ``rmdir`` needs W+X on the PARENT: the chmod above opens *d* for its own
    # children, the wrong door.  Deepest-first leaves a 555 parent; an EMPTY dir EACCES.
    if d != leaf and not os.access(d.parent, os.W_OK):
      try:
        d.parent.chmod(stat.S_IMODE(os.lstat(d.parent).st_mode) | stat.S_IWUSR)
      except OSError:
        return False
    try:
      d.rmdir()
    except OSError:
      return False
  return not leaf.exists()


SYMLINK_LOOP_WHY = (
  "it is a symlink loop that kanibako cannot resolve, so it is left in place for you to look at."
)

#: :data:`SYMLINK_LOOP_WHY` without the tail the canon Note appends itself.
CANON_SYMLINK_LOOP_WHY = "it is a symlink loop that kanibako cannot resolve"

#: The same, for a tier that resolves but HOLDS a loop.
CANON_HOLDS_LOOP_WHY = "it holds a symlink loop at %s that kanibako cannot resolve"


def _symlink_loop(path: Path) -> bool:
  """True when *path* cannot be resolved because a link on it — itself or above — LOOPS.

  ⚑ ``resolve(strict=False)`` HIDES a loop on 3.13 (it returns the path, which reads as
  an ordinary directory) and RAISES ``RuntimeError`` on 3.11.  ``realpath(strict=True)``
  raises ``OSError(ELOOP)`` on both, so a loop is NAMED, never crashed on or removed.
  Dangling is ENOENT, not a loop.  ``SYMLINK_LOOP_WHY`` is why one is kept.
  """
  import errno
  import os

  try:
    os.path.realpath(path, strict=True)
  except OSError as e:
    return getattr(e, "errno", None) == errno.ELOOP
  except RuntimeError:
    return True
  return False


def _resolved_unless_loop(path: Path) -> Path:
  """*path* resolved, or only made absolute when it runs through a symlink loop, so a caller can
  name the loop instead of crashing."""
  return path.absolute() if _symlink_loop(path) else path.resolve()


def vault_keep_reason(root: Path, vault: Path, *, early: EarlyScope) -> str:
  """The full ``why`` for keeping *vault*: named by its declaring key, if it has one.

  ⚑ COMPUTE THIS BEFORE THE RUN: the purge unlinks the root ``workset.yaml``, which IS
  the declaration, so a later reason calls the box's own arm "not a vault arm".
  """
  if _symlink_loop(vault):
    return SYMLINK_LOOP_WHY
  key = declared_repoint_key(root, vault, early=early)
  if key is not None:
    return (
      f"your {WORKSET_META_FILE} points workset.{key} here; kanibako does "
      "not remove where it points, so it is yours to remove."
    )
  return f"it is {retained_vault_reason(root, vault)} and is yours to remove."


def canon_keep_reason(root: Path, canon: Path, *, early: EarlyScope) -> str:
  """The ``why`` for keeping a canon tier — the declaration if there is one.

  :func:`report_retained_canon` appends its own ", so it is yours to remove", so
  the phrase returned here stops short of that.
  """
  if _symlink_loop(canon):
    return CANON_SYMLINK_LOOP_WHY
  if declared_repoint_key(root, canon, early=early) == _CANON_LEAF:
    return (
      f"your {WORKSET_META_FILE} points workset.{_CANON_LEAF} here and "
      "kanibako does not remove where it points"
    )
  return (
    "not the canon tier of this box"
    if _strictly_in_tree(canon, root)
    else f"not strictly inside {root}"
  )


def report_retained_canon(
  canon: Path, root: Path, why: str | None = None, *, early: EarlyScope | None = None
) -> None:
  """The retained-canon Note: the tier is outside *root*, not this box's tier, or *why*.

  ⚑ *why* comes from :func:`canon_keep_reason`, which names a box-declared repoint.
  """
  if why is None and early is not None:
    why = canon_keep_reason(root, canon, early=early)
  if why is None:
    why = (
      CANON_SYMLINK_LOOP_WHY
      if _symlink_loop(canon)
      else "not the canon tier of this box"
      if _strictly_in_tree(canon, root)
      else f"not strictly inside {root}"
    )
  print(
    f"Note: left the canon folder at {canon} in place — {why}, so it is yours to remove.",
    file=sys.stderr,
  )


def _holds_only_arms(path: Path, arms: set[Path]) -> bool:
  """True when *path* is one of *arms* (RESOLVED), or a real dir holding only such.

  ⚑ An empty dir qualifies (nothing in it to lose); a file or a symlink that is not
  an arm does not.
  """
  if _symlink_loop(path):
    return False
  if path.resolve() in arms:
    return True
  if path.is_symlink() or not path.is_dir():
    return False
  return all(_holds_only_arms(child, arms) for child in path.iterdir())


def retained_vault_reason(root: Path, vault: Path) -> str:
  """Why a :func:`standalone_vault_teardown` *retained* path stays, as a phrase.

  Outside *root* it is the user's own store; inside it is a ``vault/`` entry no
  ``workset.vault_*`` arm of the box names (e.g. one the user set to ``null``).
  ⚑ Neither phrase fits a DECLARED repoint — that one IS an arm — so those go
  through :func:`declared_repoint_key` at the report, not through here.
  """
  if _symlink_loop(vault):
    return SYMLINK_LOOP_WHY
  if _strictly_in_tree(vault, root):
    return "not a vault arm of this box"
  return f"outside {root}"


def report_retained_vault(vault: Path, why: str) -> None:
  """The retained-vault-leaf Note: name the store that stays, and why it stays.

  ⚑ ONE text for every site that leaves a vault the box no longer uses.  A keep
  that cannot name the path as the user's is just a leak, so the path is the
  subject and *why* qualifies it.
  """
  print(f"Note: left the vault at {vault} in place — {why}", file=sys.stderr)


def report_retained_snapshot_base(base: Path) -> None:
  """The Note for a box's kept snapshot base, worded from what *base* holds NOW.

  Call it after the run: the base is not a vault arm, and the purge has just taken
  the box's own store out of it.
  """
  from kanibako.snapshots import LAYOUT_MARKER_NAME

  try:
    names = {entry.name for entry in base.iterdir()}
    what = (
      "nothing"
      if not names
      else "only kanibako's snapshot layout marker"
      if names == {LAYOUT_MARKER_NAME}
      else "snapshots this purge did not delete"
    )
  except OSError:
    what = "entries kanibako could not list"
  print(
    f"Note: left the snapshot folder at {base} in place — it holds {what} and is yours to remove.",
    file=sys.stderr,
  )


def report_retained_loop(path: Path, what: str) -> None:
  """The ONE Note for a path kept because it is a loop; *what* names its role."""
  print(f"Note: left the {what} at {path} in place — {SYMLINK_LOOP_WHY}", file=sys.stderr)


def report_retained_vaults(
  root: Path, retained: Iterable[Path], *, early: EarlyScope | None = None
) -> None:
  """Print the retained-vault Note for every path in *retained* that is on disk.

  ⚑⚑ *retained* HOLDS FILES: an ``is_dir()`` test before printing drops one.  It also
  holds out-of-root arms that may not exist; a dangling symlink counts as on disk.
  ⚑ A DECLARED repoint is named by its own key: it IS this box's arm, so the
  "not an arm" phrase below would misdescribe the very folder it is asking to keep.
  """
  for vault in retained:
    if not (vault.exists() or vault.is_symlink()):
      continue
    report_retained_vault(
      vault,
      vault_keep_reason(root, vault, early=early)
      if early is not None
      else f"it is {retained_vault_reason(root, vault)} and is yours to remove.",
    )


# ---------------------------------------------------------------------------
# Failure-consistency: a LIFO unwind stack for multi-step mutations.
# ---------------------------------------------------------------------------


@dataclass
class _Unwind:
  """A LIFO stack of compensating actions for failure-consistency."""

  actions: list[Callable[[], object]] = field(default_factory=list)
  cleanups: list[tuple[Callable[[], None], Callable[[], None] | None]] = field(
    default_factory=list
  )
  #: How many cleanups have returned.
  finished: int = 0
  #: How many actions :meth:`run` saw fail: raise an ordinary exception (kept in
  #: ``errors``), or return ``False`` (restored only part, and named what it left).
  failed: int = 0
  errors: list[Exception] = field(default_factory=list)

  def push(self, action: Callable[[], object]) -> None:
    self.actions.append(action)

  def push_first(self, action: Callable[[], object]) -> None:
    """Push *action* under every other, so it runs LAST: after the destination is gone."""
    self.actions.insert(0, action)

  def on_success(
    self, action: Callable[[], None], *, interrupted: Callable[[], None] | None = None
  ) -> None:
    """Register an action to run only when the whole op succeeds.

    *interrupted* names what *action* leaves when an interrupt stops it, or stops the
    op before *action* starts.  ⚑ Includes DESTRUCTIVE work, so register only the
    relocation tail; anything else runs inside it.
    """
    self.cleanups.append((action, interrupted))

  def run(self) -> None:
    held: KeyboardInterrupt | None = None
    while self.actions:
      action = self.actions.pop()
      try:
        if action() is False:
          self.failed += 1
      except KeyboardInterrupt as exc:
        held = held or exc
      except Exception as exc:  # noqa: BLE001 - best-effort restore; kept for the report
        self.failed += 1
        self.errors.append(exc)
    if held is not None:
      raise held

  def finish(self) -> None:
    """Run success cleanups (best-effort); a raise is kept in ``errors`` for the report."""
    for action, _ in self.cleanups:
      try:
        action()
      except Exception as exc:  # noqa: BLE001 - best-effort; kept for the report
        self.failed += 1
        self.errors.append(exc)
      self.finished += 1

  def note_interrupted(self) -> None:
    """Name what each cleanup not yet returned leaves; an interrupt stopped them."""
    held: KeyboardInterrupt | None = None
    for _, interrupted in self.cleanups[self.finished :]:
      if interrupted is not None:
        try:
          interrupted()
        except KeyboardInterrupt as exc:
          held = held or exc
        except Exception:  # noqa: BLE001 - the interrupt still propagates
          pass
    if held is not None:
      raise held


def _note_unwind_errors(what: str, errors: Iterable[Exception]) -> None:
  """Print ``Note: could not <what>: <error>`` for each of *errors*."""
  for err in errors:
    print(f"Note: could not {what}: {err}", file=sys.stderr)


def _note_unwind_incomplete(
  noun: str,
  where: str,
  errors: Iterable[Exception],
  *,
  interrupted: bool,
) -> None:
  """THE Note for a rollback that left work undone: a line per error in *errors*, then *where*.

  ⚑ A step that returned ``False`` printed its own Note; one that RAISED is named here.
  """
  _note_unwind_errors(f"undo a step of the {noun}", errors)
  lead = (
    f"Interrupted: the {noun} was not fully undone"
    if interrupted
    else f"Note: the {noun} failed and was not fully undone"
  )
  print(
    f"{lead}; {where} (the notes above name what the rollback could not put back)", file=sys.stderr
  )


#: The leftover Note's default cure; an op whose re-run takes the leftover over has its own.
REMOVE_LEFTOVER = "remove it before you run it again"


def _unwind_created_root(
  path: Path,
  remove: Callable[[Path], bool] | None = None,
  *,
  cure: str = REMOVE_LEFTOVER,
) -> bool:
  """Undo a root this op created, which may be a LINK or a partial copy.

  A link is UNLINKED, never removed through; a tree goes through the escalating
  *remove* (``remove_path`` by default).  Whatever remains is named in a Note with
  *cure*, by default removing it: a leftover makes a retry fail with "Destination
  already exists".  Returns ``False`` then.
  """
  from kanibako.runtime.container import remove_path

  try:
    if (remove or remove_path)(path):
      return True
  except OSError:
    pass
  if path.exists() or path.is_symlink():
    print(f"Note: could not remove {path}, which this operation created; {cure}", file=sys.stderr)
    return False
  return True


@contextmanager
def _journal_connect(
  journal: Path | None,
  box_path: Path,
  *,
  name: str,
  workset: str | None = None,
  workspace: str | None = None,
):
  """Bracket a ``connect`` register with a J2 write-ahead journal entry (no seed step).

  Yields the ``rollback`` tally to hand ``add_project``.  ⚑ A REFUSAL clears the entry
  too (``WorksetError``, or the registry's ``ProjectError``): both fire before the
  membership write.  So does an ``OSError`` whose rollback finished, which leaves nothing
  to complete.  Any other raise, or a rollback that failed, leaves it, as a crash would.
  """
  rollback = _Unwind()
  if journal is None:
    yield rollback
    return
  from kanibako.launch import journal as journal_mod

  journal_mod.write_entry(
    journal,
    box_path,
    op="connect",
    name=name,
    mode="named",
    workset=workset,
    workspace=workspace,
  )
  try:
    yield rollback
  except (WorksetError, ProjectError):
    journal_mod.clear_entry(journal, box_path)
    raise
  except OSError:
    if not rollback.failed:
      journal_mod.clear_entry(journal, box_path)
    raise
  journal_mod.clear_entry(journal, box_path)


# Identity of the synthesized "default" workset — ⚑ VIRTUAL, never written to disk.
DEFAULT_WORKSET_ID = "__default__"
DEFAULT_WORKSET_ALIAS = "default"

# ⚑⚑ TWO SETS, BECAUSE THE TWO HALVES ARE RESERVED FOR DIFFERENT REASONS — and the
# reasons, not the comparison rule, are what the split carries.  Both are reserved by
# the three-mode model (specs/settings-keyspace-1.8.0.md §2c), and a workset name is a
# user-typed channel address, so a collision REFUSES rather than resolves.

#: Reserved IDENTIFIERS — the bare names that ALIAS the synthesized default workset.
#: ⚑ Load-bearing beyond the reservation: :func:`resolve_workset_name` and the ``workset``
#: verbs resolve exactly these two to the virtual default.  ``__PRIMARY__`` must NOT be
#: here — it names a partition, not the default workset.
RESERVED_WORKSET_IDENTIFIERS = frozenset({DEFAULT_WORKSET_ID, DEFAULT_WORKSET_ALIAS})

#: Reserved PARTITION TOKENS — the system-scope channel partition DIRECTORY names
#: (``channels/mailboxes/__PRIMARY__/``).  A named workset's token is its own name,
#: emitted verbatim as that directory, so a workset carrying one of these would land
#: inside the partition's tree.
#: 🛑 EXACT WHEREVER A TOKEN IS EMITTED INTO A PATH — a path is never case-folded.
#: ⚑ IMPORTED from :mod:`kanibako.channels.channels`, never re-spelled: that module owns
#: these two literals, and a second spelling of a path segment is a second carrier.
WORKSET_PARTITION_TOKENS = frozenset({WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE})

#: Reserved RENDERED SEGMENTS — the ``<W>`` of a primary or standalone box's container
#: name; a named workset of this name would render the same container names.
WORKSET_RENDERED_SEGMENTS = frozenset(
  {
    WORKSET_SEGMENT_PRIMARY,
    WORKSET_SEGMENT_STANDALONE,
  }
)

#: Every name a user may not give a workset — the refusal's subject, and its message.
RESERVED_WORKSET_NAMES = (
  RESERVED_WORKSET_IDENTIFIERS | WORKSET_PARTITION_TOKENS | WORKSET_RENDERED_SEGMENTS
)


def is_reserved_workset_name(name: str) -> bool:
  """Return True if *name* is reserved (cannot be a NAMED workset).

  ⚑⚑ BOTH HALVES FOLD, AND THE REASON IS THAT THIS IS A REFUSAL, NOT A PATH.
  Folding here reserves the case variants of a token; it does not fold any path —
  the tokens themselves are still emitted exactly, wherever they are emitted.

  🛑 The earlier reading — that a workset named ``__primary__`` is harmless because it
  is a distinct path segment — holds only on a case-SENSITIVE filesystem.  On macOS,
  the platform whose ``agents/Shell/`` collision opened this whole arc,
  ``channels/mailboxes/__primary__/`` IS ``channels/mailboxes/__PRIMARY__/``, and a
  named workset's token is its own name emitted verbatim
  (``channels.channels.workset_name_token``).  That is cross-partition channel leakage
  reached by a name we accepted.  Two names differing only by case ARE a collision
  (keyspec §0 "CASE INSENSITIVE for collision purposes"); refusing is both the safer
  direction and the consistent one.
  """
  return find_identifier(name, RESERVED_WORKSET_NAMES) is not None


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WorksetProject:
  """One ``boxes:`` membership row, in memory — ⚑ name + path ONLY (B7); no ``seeded`` field."""

  name: str
  # ⚑ The member's REAL workspace, and the ONE place the registry records it: the
  # external dir for a connect, ``workspaces/<name>`` for an in-tree member.
  source_path: Path


@dataclass
class Workset:
  """In-memory representation of a workset.

  ⚑ *name* comes from the caller, which got it from the GLOBAL registry — it is
  never read off disk, because nothing under *root* records it.
  """

  name: str
  root: Path
  projects: list[WorksetProject] = field(default_factory=list)
  is_default: bool = False  # True = synthesized default workset
  early_system: EarlySystem = field(kw_only=True)

  @property
  def early_scope(self) -> EarlyScope:
    """The scope this workset's early readers take: its record and its partition name."""
    return EarlyScope(self.early_system, WS_TOKEN_PRIMARY if self.is_default else self.name)

  # Convenience paths -------------------------------------------------------

  @property
  def projects_dir(self) -> Path:
    """The resolved ``workset.boxes`` dir — ⚑ RESOLVED, not composed.

    ⚑⚑ This is where box trees are CREATED, MOVED and REMOVED (``add_project``,
    ``remove_project``, ``box move``/``duplicate``/``convert``, ``box purge``),
    so composing ``<root>/boxes`` here while detection resolved the key left a
    repointed root DETECTED BUT MISLOCATED — found by the walk, then written to
    somewhere it does not live.  The launch seam has always resolved the same key
    (``settings_launch``, ``meta.box.path | {workset.boxes}/{meta.box.name}``); this
    property is a FACE on that answer, never a second one.
    """
    return resolve_workset_boxes(
      self.root,
      load_workset_settings_doc(self.root),
      early=self.early_scope,
    )

  @property
  def workspaces_dir(self) -> Path | None:
    """The resolved ``workset.workspaces`` dir; ``None`` when the root nulls it (no dir, Q106)."""
    # ⚑ RESOLVED, not composed (§3.3: real and USED), and read off the root's file
    # each time, like :attr:`projects_dir`.
    return resolve_workset_workspaces(
      self.root,
      load_workset_settings_doc(self.root),
      early=self.early_scope,
    )

  def require_workspaces_dir(self, what: str) -> Path:
    """:attr:`workspaces_dir` for an op that needs the dir; a null REFUSES, naming *what*."""
    workspaces = self.workspaces_dir
    if workspaces is None:
      null_file = _workspaces_null_file(self.root, early=self.early_scope)
      raise WorksetError(ERR_WORKSET_NULL_WORKSPACES % (null_file, what))
    return workspaces

  @property
  def vault_dir(self) -> Path:
    # ⚑ THE SKELETON DIR ONLY — the non-key shared DEFAULT PARENT (see _VAULT_LEAF).
    # 🛑 DO NOT build a vault path off this: ``vault_ro``/``vault_rw`` are repointable
    # keys, so ``vault_dir / "ro"`` answers a key the settings file may have moved.
    # Use ``vault_ro_dir`` / ``vault_rw_dir`` (or ``resolve_workset_vault_pair``).
    return self.root / _VAULT_LEAF

  @property
  def vault_ro_dir(self) -> Path | None:
    """The resolved ``workset.vault_ro`` — ⚑ RESOLVED, not composed; ``None`` when nulled."""
    return resolve_workset_vault_ro(
      self.root,
      load_workset_settings_doc(self.root),
      early=self.early_scope,
    )

  @property
  def vault_rw_dir(self) -> Path | None:
    """The resolved ``workset.vault_rw`` — ⚑ RESOLVED, not composed; ``None`` when nulled."""
    return resolve_workset_vault_rw(
      self.root,
      load_workset_settings_doc(self.root),
      early=self.early_scope,
    )

  @property
  def logs_dir(self) -> Path | None:
    """The resolved ``workset.logs`` dir — ⚑ RESOLVED, not composed; primary/named ONLY.

    ``None`` when ``workset.logs`` is a present ``<None>`` (no logs dir).

    ⚑ The helper-log MOUNT has always been the spec spelling
    (``{workset.logs}/{meta.box.name}.jsonl``, ``data/rom/settings/core-defaults.yaml``), so a
    composed leaf here made the hub WRITE somewhere the box does not READ — the
    split migration M-14 records.  A standalone box's log is resolved through the
    same key with ``standalone=True``, not through this property, which is a
    WORKSET face; see ``settings/paths.py::helper_log_path``.
    """
    return resolve_workset_logs(
      self.root,
      load_workset_settings_doc(self.root),
      early=self.early_scope,
    )

  @property
  def settings_path(self) -> Path:
    """The workset-tier settings file — SETTINGS ONLY, and may not exist."""
    return self.root / WORKSET_META_FILE

  @property
  def registry_path(self) -> Path:
    """The per-workset ``registry.yaml`` — the ``boxes:`` membership, and only that."""
    return workset_registry.workset_registry_path(self.root)


# ---------------------------------------------------------------------------
# Loading a workset: its NAME comes from the caller (who read it out of the global
# registry), its MEMBERS from the root registry.yaml's ``boxes:`` section, and its
# settings from the root workset.yaml.  ⚑ Neither file under the root is read for
# identity — there is none there to read.
# ---------------------------------------------------------------------------


def _load_workset(root: Path, name: str, *, early_system: EarlySystem) -> Workset:
  """Build the :class:`Workset` for the globally-registered *name* rooted at *root*."""
  # ⚑ A root still carrying a RETIRED identity table refuses here, with the named
  # cure — it is the load path, not detection, that a 1.6/1.7 user reaches first
  # (their workset IS globally registered, so detection resolves it fine).
  refuse_reserved_registered_name(name, root, early_system=early_system)
  refuse_retired_workset_identity(root)
  load_workset_settings_doc(root)  # refuses a malformed file, naming it
  ws = Workset(name=name, root=root, early_system=early_system)
  registry_path = ws.registry_path
  # ⚑ Members come from ``boxes:``, which is the WHOLE of what that file holds, and
  # the path is recorded there exactly once.
  ws.projects = [
    WorksetProject(name=box_name, source_path=Path(box_path))
    for box_name, box_path in workset_registry.load_workset_boxes(
      registry_path,
    ).items()
  ]
  return ws


def refuse_reserved_registered_name(name: str, root: Path, *, early_system: EarlySystem) -> None:
  """RAISE when a pre-1.8 registry entry carries a name ``create`` now refuses.

  ⚑ An ALIAS VARIANT (``Default``, ``__DEFAULT__``) is refused like any other, and
  is cured by DIRECTORY: the ``workset`` verbs resolve ``default``/``__default__``
  to the synthesized default workset, so a name-based step answers for PRIMARY and
  never reaches the registered entry.
  """
  if not is_reserved_workset_name(name):
    return
  aliased = find_identifier(name, RESERVED_WORKSET_IDENTIFIERS) is not None
  try:
    boxes = workset_registry.load_workset_boxes(
      Workset(name=name, root=root, early_system=early_system).registry_path
    )
  except (ConfigError, WorksetError):
    boxes = {}
  in_tree = sorted(
    (box, Path(path).relative_to(root))
    for box, path in boxes.items()
    if Path(path).is_relative_to(root)
  )
  # ⚑ FORCED for an alias variant even when the directory is already legal: the
  # reserved thing is the registry KEY, and the basename is the only name a
  # re-import can give this tree.
  moved = aliased or is_reserved_workset_name(root.name)
  new_root = root.parent / "<new name>" if moved else root
  steps = [] if aliased else [f"kanibako workset rm {shlex.quote(name)} --force"]
  if moved:
    steps.append(f"mv {shlex.quote(str(root))} {shlex.quote(str(new_root))}")
  verb = "box remap --force" if moved else "box info"
  steps += [f"cd {shlex.quote(str(new_root / rel))} && kanibako {verb}" for _, rel in in_tree]
  if in_tree:
    tail = f"The first '{verb}' imports the working set under its directory name; " + (
      "each one re-points that box at its new path."
      if moved
      else "the others only confirm each box."
    )
  else:
    steps.append(f"cd {shlex.quote(str(new_root))} && kanibako box info")
    tail = (
      "The last command imports the working set under its directory name, "
      "then exits 1 saying you are not inside a project, which is expected."
    )
  if aliased:
    refused = ", ".join(sorted(RESERVED_WORKSET_IDENTIFIERS))
    why = (
      f"{refused} are the `workset` verbs' name for the primary working set, so "
      "no working set may take one, and `workset rm` reaches primary rather than "
      "this entry"
    )
    heading = "'A working set named default must be registered again'"
  else:
    refused = ", ".join(sorted(RESERVED_WORKSET_NAMES - RESERVED_WORKSET_IDENTIFIERS))
    why = (
      f"{refused} belong to the primary and standalone partitions: a working set "
      f"called '{name}' would share their container names (kb-<workset>-<box>) or "
      "channel addresses"
    )
    heading = "'A working set named primary or standalone must be registered again'"
  raise ReservedWorksetNameError(
    f"Working set '{name}' is registered under a reserved name. The names "
    f"{why}. kanibako 1.7 accepted the name; 1.8 refuses it. Register the working "
    "set again under its directory name; its files stay where they are:\n"
    + "".join(f"  {step}\n" for step in steps)
    + f"{tail} See MIGRATION.md, {heading}."
  )


def refuse_retired_workset_identity(root: Path) -> None:
  """RAISE when *root*'s workset.yaml still carries a RETIRED workset IDENTITY table.

  ⚑ DETECTED ONLY SO IT CAN BE DIAGNOSED.  v1.8.0 is a clean break: there is no compat
  read and no auto-migration.  Reading past this table is exactly the silent failure —
  1.8.0 takes the file for ordinary settings, drops the table with a generic warning
  and never looks at the `projects` list beside it, so a legacy workset's members stop
  resolving with nothing printed to say why.  BOTH retired spellings are caught:
  1.6.0/1.7.x wrote ``workset.meta``, and the unreleased 1.8.0 tree briefly wrote
  ``meta.workset``.
  """
  try:
    data = load_workset_settings_doc(root)
  except ConfigError:
    return
  if data is None:
    return
  workset_tbl = data.get("workset")
  meta_tbl = data.get("meta")
  if isinstance(workset_tbl, Mapping) and isinstance(workset_tbl.get("meta"), Mapping):
    retired = "workset.meta"
    tail = (
      "the top-level `workset:` table is still where this workset's own SETTINGS "
      "live (`workset.bindings`, `workset.workspaces`, `workset.auth`, …), so "
      "delete only the `meta:` table from inside it"
    )
  elif isinstance(meta_tbl, Mapping) and isinstance(meta_tbl.get("workset"), Mapping):
    retired = "meta.workset"
    tail = (
      "the top-level `meta:` table holds nothing a workset root may set, so delete the whole of it"
    )
  else:
    return
  path = root / WORKSET_META_FILE
  raise LegacyWorksetIdentityError(
    f"'{retired}' is a RETIRED location for a named workset's identity table "
    f"and is still the shape of {path}.\n"
    f"THE RULE: a workset has NO identity table on disk under its root. Its name "
    f"lives in ONE place — the `worksets:` section of the global registry, which "
    f"maps that name to this directory and is what `kanibako workset list` reads. "
    f"This file carries SETTINGS ONLY, is sparse, and may be absent entirely; "
    f"MEMBERSHIP lives in {root / 'registry.yaml'} as flat `boxes:` entries, "
    f"`name: path`. kanibako 1.6.0 and 1.7.x kept the name, a `created` stamp and a "
    f"`projects` list here, so every workset root those releases created carries "
    f"them. Refusing rather than running: 1.8.0 reads this file as ordinary "
    f"settings, so it would drop the table as an unsettable `meta` namespace and "
    f"ignore the `projects` list — your connected boxes would stop resolving with "
    f"nothing printed to say why.\n"
    f"  Fix, BY HAND:\n"
    f"\n"
    f"    1. Each entry of the `projects` LIST becomes one flat entry of the "
    f"`boxes:` section in {root / 'registry.yaml'}, keyed by its `name`, with its "
    f"`source_path` as the value. An entry already there is already correct — leave "
    f"it:\n"
    f"\n"
    f"         boxes:\n"
    f"           <project name>: <its source_path>\n"
    f"\n"
    f"    2. Delete the `{retired}` table from {path} — name, created stamp and "
    f"projects together. NOTHING replaces it: `workset create` already registered "
    f"this workset under its name in the global registry, and `created` is not "
    f"recorded anywhere in 1.8.0.\n"
    f"\n"
    f"  Everything else in {path} stays put: {tail}. If nothing is left, delete the "
    f"file outright — a workset root no longer needs one. kanibako 1.8.0 ships no "
    f"automatic migration for this — see MIGRATION.md "
    f'§ "A workset root no longer carries an identity table; an un-migrated root refuses".'
  )


# ---------------------------------------------------------------------------
# Global worksets registry: the ``worksets`` section of ``config.registry``.
# ⚑⚑ THE ONE PLACE A WORKSET'S NAME IS RECORDED — its identity, not an index of it.
# ⚑ ONE section in ONE file; ``register_name``/``unregister_name`` are the SOLE writers.
# ---------------------------------------------------------------------------


class WorksetRows(NamedTuple):
  """What :func:`read_worksets` reads: the live map and the rows the drop removes."""

  live: dict[str, Path]
  #: ``{name: (root, why)}`` — a ``default`` alias variant whose root is gone or is the
  #: primary store: no command could address it, and its cure would move the primary store.
  droppable: dict[str, tuple[Path, str]]


def read_worksets(std: StandardPaths) -> WorksetRows:
  """Read the global worksets registry; ⚑ NO WRITE — droppable rows are filtered in memory."""
  section = registry_store.load_section(std.registry, "worksets")
  primary = std.primary_workset.resolve()
  live: dict[str, Path] = {}
  droppable: dict[str, tuple[Path, str]] = {}
  for name, root_str in section.items():
    root = Path(root_str)
    if find_identifier(name, RESERVED_WORKSET_IDENTIFIERS) is not None:
      if registry_store._metadata_definitively_gone(root_str):
        droppable[name] = (root, "its directory no longer exists")
        continue
      if _resolved_unless_loop(root) == primary:  # ⚑ resolve() raises on a loop on 3.11
        droppable[name] = (root, "it is the primary working set, which `default` already names")
        continue
    live[name] = root
  return WorksetRows(live, droppable)


def _drop_worksets(std: StandardPaths, droppable: dict[str, tuple[Path, str]]) -> None:
  """Unregister each droppable row with a note; only the registry file is written."""
  for name, (root, why) in droppable.items():
    if (std.registry, name) in _UNDROPPABLE:
      continue
    try:
      unregister_name(std.registry, name, section="worksets", exact=True)
    except OSError as exc:
      _UNDROPPABLE.add((std.registry, name))
      print(
        f"Warning: working set '{name}' ({root}) could not be removed "
        f"from the registry ({why}): {exc}",
        file=sys.stderr,
      )
    else:
      print(
        f"Note: removed working set '{name}' ({root}) from the "
        f"registry: {why}. No file under it was touched.",
        file=sys.stderr,
      )


#: Droppable ``(registry, key)`` pairs whose write failed: left out in memory, warned once.
_UNDROPPABLE: set[tuple[Path, str]] = set()


def _logs_walk_targets(std: StandardPaths) -> dict[str, Path]:
  """Every workset a ``workset.logs`` share can reach: the registered ones, PLUS the
  default, which is VIRTUAL (no registry row) and owns the primary log directory."""
  return {**list_worksets(std), DEFAULT_WORKSET_ID: std.primary_workset}


def _walk_scope(system: EarlySystem, name: str, root: Path) -> EarlyScope:
  """*name*'s scope, through :attr:`Workset.early_scope` (the default reads as primary)."""
  return Workset(
    name, root, is_default=name == DEFAULT_WORKSET_ID, early_system=system
  ).early_scope


def _shown(name: str) -> str:
  """*name* as a message says it: the VIRTUAL default's id reads as its alias."""
  return DEFAULT_WORKSET_ALIAS if name == DEFAULT_WORKSET_ID else name


def _with_logs(doc: Mapping[str, Any] | None, value: object) -> dict:
  """A copy of *doc* as a write leaves its ``workset.logs`` slot: *value*, or gone if ``UNSET``."""
  from kanibako.settings.config_keys import _KEY_ROUTES

  sections, slot = _KEY_ROUTES["workset.logs"]
  out = copy.deepcopy(dict(doc)) if isinstance(doc, Mapping) else {}
  node = out
  for section in sections:
    child = node.get(section)
    child = dict(child) if isinstance(child, Mapping) else {}
    node[section] = child
    node = child
  if value is UNSET:
    node.pop(slot, None)
  else:
    node[slot] = value
  return out


def find_logs_share(
  std: StandardPaths,
  *,
  value: object,
  scope: str,
  target_name: str | None = None,
  target_root: Path | None = None,
) -> "tuple[tuple[str, ...], Path] | None":
  """Which worksets one ``workset.logs`` write puts on ONE directory, and which.

  Keyspec § 0 "Per-owner resources": two instances whose own values name one per-owner
  resource share it, refused by name unless ``--force``.  *value* is what the write
  leaves: a string, ``None`` for ``--null``, ``UNSET`` for a reset or a new workset.
  *scope* ``workset`` rewrites *target_name* alone (*target_root* may add it to the
  walk, as a new workset); ``system`` rewrites the tier every workset WITHOUT its own
  value reads.  Only a share holding a workset the write MOVES is reported, so a write
  that changes nothing is never refused for a share already there.

  ⚑ Every workset resolves through :func:`resolve_workset_logs`, the rule its boxes
  read, and paths compare RESOLVED: ``{meta.workset.path}/../shared`` differs per file
  and is one directory.  No logs dir (``None``) is no member; an unresolvable value is
  skipped, as its verbs already refuse it.
  """
  targets = _logs_walk_targets(std)
  joins = scope == "workset" and target_name not in targets
  if scope == "workset" and target_name is not None and target_root is not None:
    targets[target_name] = target_root
  system = std.early_system
  if scope == "system":
    tier: dict[str, Any] = {k: v for k, v in system.tier.items() if k != "workset.logs"}
    if value is not UNSET:
      tier["workset.logs"] = value
    system = replace(system, tier=tier)
  groups: dict[Path, list[tuple[str, bool]]] = {}
  for name, root in targets.items():
    try:
      doc = load_doc(root / WORKSET_META_FILE)
      driven = False
      if scope == "workset" and name == target_name:
        moved = _with_logs(doc, value)
        driven = joins or (_stored_repoint(moved, _LOGS_LEAF) != _stored_repoint(doc, _LOGS_LEAF))
        doc = moved
      elif scope == "system":
        driven = system.tier != std.early_system.tier and _stored_repoint(doc, _LOGS_LEAF) is UNSET
      resolved = resolve_workset_logs(root, doc, early=_walk_scope(system, name, root))
    except (ConfigError, SettingsError):
      continue
    if resolved is not None:
      groups.setdefault(Path(resolved).resolve(), []).append((name, driven))
  for resolved, members in groups.items():
    if len(members) > 1 and any(is_driven for _, is_driven in members):
      return tuple(sorted(_shown(name) for name, _ in members)), resolved
  return None


def logs_share_refusal(
  canonical_key: str,
  value: object,
  std: StandardPaths,
  *,
  force: bool,
  scope: str,
  target_name: str | None = None,
  target_root: Path | None = None,
) -> str | None:
  """The message a ``workset.logs`` write door prints INSTEAD of writing, or ``None``.

  *value*, *scope* and the target are :func:`find_logs_share`'s.
  """
  if force or canonical_key != "workset.logs":
    return None
  hit = find_logs_share(
    std,
    value=value,
    scope=scope,
    target_name=target_name,
    target_root=target_root,
  )
  if hit is None:
    return None
  names, shared = hit
  sharers = ", ".join(f"'{name}'" for name in names)
  if value is not UNSET:
    what = f"workset.logs = {value!r} ({scope} scope)"
  elif scope == "system":
    what = "workset.logs with no system-scope value"
  else:
    what = f"working set '{_shown(target_name or '')}' with no workset.logs of its own"
  return (
    f"Error: nothing was written: {what} resolves "
    f"to the SAME log directory ({shared}) for working sets {sharers}. Two working "
    "sets on one log directory share its files: a same-named box in either writes "
    "into the other's, and a purge in either reaches the other's. Point them at "
    "directories of their own, or pass --force to share it deliberately."
  )


def _logs_share_partners(
  std: StandardPaths,
  logs_dir: Path,
  box: str,
  *,
  workset_root: Path | None,
) -> tuple[str, ...]:
  """Working sets whose claim on *box*'s log file here is indistinguishable from this one's.

  A working set is a PARTNER when its own resolved ``workset.logs`` IS *logs_dir* AND
  its ``boxes:`` membership holds a box of *box*'s name: the two boxes then name ONE
  file (``{workset.logs}/<name>.jsonl``), and nothing inside says which box wrote it.
  *workset_root* is the purging verb's own workset, excluded by resolved root; a
  STANDALONE root is not in the walk, so every match is somebody else.

  ⚑ Resolved through :func:`resolve_workset_logs` and compared RESOLVED, like
  :func:`find_logs_share`.  A partner whose membership table cannot be READ still
  COUNTS: a table that will not open cannot prove the file is no one else's.  A
  workset with no logs dir, or one that will not resolve, writes no logs: no partner.
  """
  if _symlink_loop(Path(logs_dir)):
    return ()
  try:
    shared = Path(logs_dir).resolve()
  except OSError:
    return ()
  partners: list[str] = []
  for name, root in _logs_walk_targets(std).items():
    if _symlink_loop(root) or (
      workset_root is not None and Path(workset_root).resolve() == root.resolve()
    ):
      continue
    try:
      early = _walk_scope(std.early_system, name, root)
      doc = load_doc(root / WORKSET_META_FILE)
      resolved = resolve_workset_logs(root, doc, early=early)
    except (ConfigError, SettingsError, OSError):
      continue
    if resolved is None or _symlink_loop(Path(resolved)) or Path(resolved).resolve() != shared:
      continue

    try:
      boxes = workset_registry.load_workset_boxes(workset_registry.workset_registry_path(root))
    except (ConfigError, SettingsError, OSError):
      partners.append(_shown(name))
      continue
    if find_identifier(box, boxes) is not None:
      partners.append(_shown(name))
  return tuple(partners)


def purge_box_logs(
  std: StandardPaths,
  logs_dir: Path | None,
  box: str,
  *,
  workset_root: Path | None,
  files: list[Path] | None = None,
) -> bool:
  """Delete :func:`box_logs_to_purge`'s files; True when none of them is left.

  *files* is that list when the caller already listed it, so its Notes print once.
  A file that will not go is named and left, never raised: it is a failed purge, which
  the caller owns.
  """
  all_removed = True
  if files is None:
    files = box_logs_to_purge(std, logs_dir, box, workset_root=workset_root)
  for log_file in files:
    try:
      log_file.unlink()
    except OSError as exc:
      sys.stdout.flush()
      print(
        f"Warning: could not remove the log {log_file}: {exc.strerror or exc} — left in place.",
        file=sys.stderr,
      )
      all_removed = False
  return all_removed


def box_logs_to_purge(
  std: StandardPaths,
  logs_dir: Path | None,
  box: str,
  *,
  workset_root: Path | None,
) -> list[Path]:
  """*box*'s log files a purge deletes: any file another working set shares by name is kept.

  Keyspec § 0 "Per-owner resources": *"a forced share's destructive verb removes only
  its own instance's part."*  A per-box log is named by BOX NAME ALONE, so once two
  working sets are forced onto one ``workset.logs`` directory, same-named boxes in the
  two of them map to the SAME file.  That file is not attributable, so it is KEPT and
  REPORTED and never deleted — the same rule a destructive verb already follows for any
  path outside the box's own root.  A differently-named box's files are never in this
  verb's way, so a forced share costs the ordinary purge nothing.

  *workset_root* is the root of the workset the verb acts for: ``ws.root``, the
  primary workset, or a STANDALONE box's own root (never in the walk, so it excludes
  nothing).  A kept file is reported here and is NOT in the list, because every
  caller prints the list as "Removed".
  """
  if logs_dir is None:
    return []
  partners = _logs_share_partners(std, logs_dir, box, workset_root=workset_root)
  keep: tuple[Path, ...] = ()
  if partners:
    keep = tuple(box_log_files(logs_dir, box))
    who = ", ".join(f"'{name}'" for name in partners)
    for path in keep:
      print(
        f"Note: kept {path} — working set(s) {who} resolve workset.logs to this "
        f"same directory and hold a box named {box!r}, so the file is not "
        "attributable to one box. Delete it yourself if this purge owns it.",
        file=sys.stderr,
      )
  return box_logs_to_remove(logs_dir, box, keep=keep)


# ---------------------------------------------------------------------------
# The workset SKELETON — ⚑⚑ ONE definition with TWO consumers: ``create_workset``
# STAMPS these dirs and :func:`is_workset_skeleton` TESTS for them, so the stamp and
# the test cannot drift (system-design § "Detection & import").
# ---------------------------------------------------------------------------


def _workset_skeleton_dirs(root: Path, *, early: EarlyScope) -> tuple[Path, ...]:
  """The four dirs a workset root is made of — ⚑ three RESOLVED, ``vault`` alone literal.

  Fewer when ``workset.logs`` or ``workset.workspaces`` is a present ``<None>``: that key
  then names no dir.
  """
  # ⚑⚑ THE RESOLVED DIRS ARE THE LOCATOR (system-design, NAMED arm of "Detect =
  # ancestor-walk").  ``boxes``, ``workspaces`` and ``logs`` are all declared,
  # repointable workset keys, so the locator must be what each one RESOLVES to, or a
  # repointed root is invisible to detection.
  # ⚑ ``vault`` is the one literal, and correctly so — no key names it (see _VAULT_LEAF).
  # ⚑ ONE read feeds all three resolutions; reading workset.yaml per key would open a
  # window for the three to disagree about the same file.  At create time *root* has no
  # workset.yaml yet, so each leaf is the system file's value, else its default.
  settings_doc = load_workset_settings_doc(root)
  dirs = (
    resolve_workset_boxes(root, settings_doc, early=early),
    resolve_workset_workspaces(root, settings_doc, early=early),
    root / _VAULT_LEAF,
    resolve_workset_logs(root, settings_doc, early=early),
  )
  return tuple(d for d in dirs if d is not None)


def is_workset_skeleton(root: Path, *, early: EarlyScope) -> bool:
  """True when *root* carries the WHOLE skeleton — ⚑ the NAMED-root detection primitive.

  ⚑ Presence-only, and it names nothing.  A workset root records no name anywhere
  (its identity is the global registry's ``worksets:`` entry), so this answers only
  *"is a workset here"* — system-design § "Detection & import": detection and naming are
  two questions, and answering one does not answer the other.  ``_is_standalone_meta_dir``
  also names nothing.
  ⚑ ALL are required (four, fewer under a ``<None>`` ``workset.logs`` or
  ``workset.workspaces``): any one of them alone is an ordinary directory name.
  ⚑ Three of the four are RESOLVED through their workset keys, so this finds a root
  that has repointed ``workset.boxes``, ``workset.workspaces`` or ``workset.logs``.
  """
  try:
    dirs = _workset_skeleton_dirs(root, early=early)
  except ConfigError:
    # ⚑ A malformed file is no skeleton; an unreadable one is no answer: refuse it.
    refuse_unreadable_file(root / WORKSET_META_FILE)
    return False
  return all(subdir.is_dir() for subdir in dirs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_workset(
  name: str,
  root: Path,
  std: StandardPaths,
  *,
  force_logs_share: bool = False,
) -> Workset:
  """Create a new workset directory structure and register it globally.

  *force_logs_share* accepts a ``workset.logs`` directory another workset resolves to.
  """
  if not name:
    raise WorksetError("Workset name must not be empty.")

  # ⚑ The name rule (spec §0) — the ONE gate, typed (``--name``) or derived alike.
  flaw = name_flaw(name)
  if flaw is not None:
    from kanibako.launch.box_identity import name_spelling

    offer = name_spelling(name)
    spelled = offer[0] if offer is not None else "<new-name>"
    raise WorksetError(
      ERR_WORKSET_NAME
      % (
        name,
        flaw,
        NAME_CHARSET,
        f"kanibako workset create {shlex.quote(str(root))} --name {spelled}",
      )
    )

  if is_reserved_workset_name(name):
    raise WorksetError(
      f"Workset name '{name}' is reserved and cannot be used. The names "
      f"{', '.join(sorted(RESERVED_WORKSET_NAMES))} are reserved sentinels "
      "for the primary and standalone partitions. Choose another name."
    )

  # ⚑ Same-kind uniqueness (D-B3): refuse, never auto-suffix.  A primary box of the same
  # name is a separate namespace (spec § Detection & import), so it is not consulted.
  # ⚑ Case-blind (§0, ⚑ NAMING RULES): ``Foo`` collides with a registered ``foo``.
  registry = list_worksets(std)
  held = find_identifier(name, registry)
  if held is not None:
    # ⚑ Name the STORED spelling when it differs.  A user refused for a name that
    # does not appear in ``workset list`` cannot otherwise tell what they hit.
    as_stored = "" if held == name else f" as '{held}'"
    raise WorksetError(
      f"Workset name '{name}' is already in use{as_stored} (registered at "
      f"{registry[held]}). Workset names must be unique; choose a "
      "different name."
    )

  root = Path(literal_path(root))
  if root.exists():
    raise WorksetError(f"Workset root already exists: {root}")
  refuse_inherited_per_owner(root, EarlyScope(std.early_system, name), doc=None)
  shared = logs_share_refusal(
    "workset.logs",
    UNSET,
    std,
    force=force_logs_share,
    scope="workset",
    target_name=name,
    target_root=root,
  )
  if shared is not None:
    raise WorksetError(shared.removeprefix("Error: "))

  # Multi-step: disk skeleton, then the ONE global registration.  A crash between
  # them would orphan dirs, so unwind in reverse: all-or-nothing.
  unwind = _Unwind()
  try:
    # ⚑ Skeleton = the FOUR dirs of :func:`_workset_skeleton_dirs`, which is also
    # what detection tests for; auth/channels are created lazily elsewhere.  NO file
    # is written at all — no workset.yaml (a workset root need not have one) and
    # no registry.yaml (a workset with no members has no membership to record).
    root.mkdir(parents=True)
    unwind.push(partial(_unwind_created_root, root))
    ws = Workset(name=name, root=root, early_system=std.early_system)
    # ⚑ *root* was just created empty, so it has no workset.yaml; a resolved leaf
    # differs from its default only through the system file.  A leaf outside *root*
    # (or at it) is the user's directory, never created here; the rest lie under
    # *root*, so ``parents`` and ``exist_ok`` can only fill in this root: a system
    # value may nest a leaf or name one twice.
    for subdir_path in _workset_skeleton_dirs(root, early=ws.early_scope):
      if _path_in_tree(subdir_path, root) and subdir_path.resolve() != root.resolve():
        subdir_path.mkdir(parents=True, exist_ok=True)

    def _drop_workset() -> None:
      if read_names(std.registry).get("worksets", {}).get(name) == str(root):
        unregister_name(std.registry, name, section="worksets", exact=True)

    unwind.push(_drop_workset)
    # ⚑⚑ THE REGISTRATION IS THE CREATION: this line is what makes the directory a
    # workset, because the name→root entry it writes IS the workset's identity.
    # ⚑ ONE section serves BOTH name lookup AND discovery/list — hence one call.
    register_name(std.registry, name, str(root), section="worksets")
  except BaseException as exc:
    unwind.run()
    if unwind.failed:
      _note_unwind_incomplete(
        "workset creation",
        f"check '{name}' at {root}",
        unwind.errors,
        interrupted=isinstance(exc, KeyboardInterrupt),
      )
    raise

  return ws


def load_workset(root: Path, name: str, *, early_system: EarlySystem) -> Workset:
  """Load the workset registered as *name* at *root* (raises ``WorksetError`` if absent).

  ⚑ *name* is REQUIRED and comes from the global registry's ``worksets:`` section —
  the caller reached *root* through that mapping, and it is the only record of the
  name there is.  ⚑ An unreadable or looped *root* refuses, naming it (``ConfigError``);
  only a definitively absent one is ``WorksetError``.
  """
  root = Path(literal_path(root))
  if not is_dir_unless_gone(root):
    raise WorksetError(f"Workset root does not exist: {root}")
  return _load_workset(root, name, early_system=early_system)


def list_worksets(std: StandardPaths) -> dict[str, Path]:
  """Return ``{name: root_path}`` for all registered worksets — ⚑ the default workset is NOT here.

  ⚑ :func:`read_worksets` plus the drop: a droppable row is unregistered with a note.
  """
  rows = read_worksets(std)
  _drop_worksets(std, rows.droppable)
  return rows.live


def default_workset(std: StandardPaths) -> Workset:
  """Synthesize the default workset — ⚑ VIRTUAL: no registry write, no identity on disk."""
  from kanibako.settings.paths import load_primary_boxes

  projects_map = load_primary_boxes(std.primary_workset)
  projects = [
    WorksetProject(name=name, source_path=Path(path)) for name, path in projects_map.items()
  ]

  return Workset(
    name=DEFAULT_WORKSET_ID,
    root=std.primary_workset,
    projects=projects,
    is_default=True,
    early_system=std.early_system,
  )


def resolve_workset_name(name: str, std: StandardPaths) -> Workset:
  """Resolve a workset *name* to a :class:`Workset` (``default``/``__default__`` → synthesized)."""
  if find_identifier(name, RESERVED_WORKSET_IDENTIFIERS) is not None:
    return default_workset(std)
  registry = list_worksets(std)
  # ⚑ Resolve THROUGH the stored spelling: the Workset must carry the name as
  # registered, not as typed, or everything derived from it disagrees with the registry.
  stored = find_identifier(name, registry)
  if stored is None:
    raise WorksetError(f"Working set '{name}' is not registered.")
  return load_workset(registry[stored], stored, early_system=std.early_system)


def delete_workset(
  name: str,
  std: StandardPaths,
  *,
  remove_files: bool = False,
  keeps: Callable[[Path], bool] | None = None,
) -> Path:
  """Unregister a workset and optionally remove its tree; returns the deleted root path.

  *keeps* is asked of each box tree and of the root before any of them is deleted;
  a path it answers True for is left in place (the caller has named it).
  """
  registry = list_worksets(std)
  stored = find_identifier(name, registry)
  if stored is None:
    raise WorksetError(f"Workset '{name}' is not registered.")

  root = registry[stored]
  purge = remove_files and root.is_dir()
  # ⚑ RESOLVED BEFORE THE UNREGISTER: a store that refuses (a null ``workset.boxes``)
  # must stop the purge while the workset is still registered, not after.  ⚑⚑ AND AN
  # UNREADABLE FILE IS NOT A REFUSAL: the unknowable repoint falls back to the
  # DEFAULT store.
  try:
    ws_settings = load_workset_settings_doc(root) if purge else None
  except ConfigError:
    ws_settings = None
  early = EarlyScope(std.early_system, stored)
  if purge:
    refuse_inherited_per_owner(root, early, doc=ws_settings)
  boxes_dir = resolve_workset_boxes(root, ws_settings, early=early) if purge else None

  kept: set[Path] = set()
  if boxes_dir is not None:
    # ⚑⚑ BOX TREES FIRST (J-7), BEFORE THE UNREGISTER: a whole-root rmtree hits the
    # root-owned 555 canon skeleton, and a refusal must leave the workset re-runnable.
    from kanibako.runtime.container import remove_box_tree

    # ⚑ RESOLVED, not composed — ``workset.boxes`` is repointable, and looking under
    # the default leaf skipped the escalation for a repointed store, so the rmtree
    # below then failed on the very skeleton this pass exists to clear.
    # ⚑⚑ STRICT-BELOW, and it does NOT widen what is deleted: this pass is a
    # PRE-PASS FOR ``rmtree(root)``, so it is owed exactly to the trees that call
    # will reach.  A store the user pointed OUTSIDE *root* is not reached by either,
    # before this change or after — cf. ``standalone_vault_teardown``, which draws
    # the same line for the same reason.  KNOWN AND UNCLOSED: those trees outlive
    # ``workset rm --purge``; closing that needs a retained-path report, not a wider
    # rmtree.
    box_trees = (
      [t for t in sorted(boxes_dir.iterdir()) if t.is_dir() and not t.is_symlink()]
      if (boxes_dir.is_dir() and not boxes_dir.is_symlink() and _strictly_in_tree(boxes_dir, root))
      else []
    )
    # ⚑ Asked of EVERY path before ONE is deleted: a box tree holding another box's
    # recorded workspace is kept, and so is the root, which holds it too.
    kept = {path for path in (*box_trees, root) if keeps is not None and keeps(path)}
    for box_tree in box_trees:
      if box_tree not in kept and not remove_box_tree(box_tree):
        raise StoreRemovalError(
          f"could not remove {box_tree}; try: podman unshare rm -rf {shlex.quote(str(box_tree))}",
          box_tree,
        )

  # Drop the ONE ``worksets`` entry, by the STORED spelling.  Idempotent: a missing
  # entry is a no-op.
  unregister_name(std.registry, stored, section="worksets")

  # ⚑ The root goes LAST: only after the registry is clean.
  if boxes_dir is not None and root not in kept:
    import shutil

    shutil.rmtree(root)

  return root


def source_in_tree(ws: Workset, source_path: Path) -> bool:
  """True when *source_path* lies under *ws*'s root — an IN-TREE member, not an external one."""
  return _path_in_tree(source_path, ws.root)


def is_in_tree_workspace(ws: Workset, path: Path) -> bool:
  """True when *path* is one of *ws*'s own workspaces — the ONE in-tree test.

  In-tree is ``ws.root`` OR the resolved ``workset.workspaces`` dir (a user key, so
  repointed it puts a member's leaf outside the root, and a relocation reads it as
  EXTERNAL).  Each root is tested twice — *path* resolved, and its PARENT
  resolved with the leaf not followed — so a symlinked leaf is in-tree.
  ⚑ A LOOPED root has no ``workset.yaml`` to read and holds nothing: never in tree.
  """
  if _symlink_loop(ws.root):
    return False
  roots = [ws.root]
  workspaces = ws.workspaces_dir
  if workspaces is not None:
    roots.append(workspaces)
  for root in roots:
    if _path_in_tree(path, root) or _path_in_tree(path.parent, root):
      return True
  return False


def _path_in_tree(path: Path, root: Path) -> bool:
  """True when *path* lies under *root*, both resolved (the in-tree test).
  ⚑ A LOOP on either answers False: a caller where True means REFUSE tests it first."""
  if _symlink_loop(path) or _symlink_loop(root):
    return False
  return path.resolve().is_relative_to(root.resolve())


def _strictly_in_tree(path: Path, root: Path) -> bool:
  """True when the entry *path* names sits STRICTLY below *root*.

  A link AT *path* is judged where it sits, anything else resolved: a linked parent
  leading out of *root* puts *path* outside.  A caller that descends into *path* must
  also refuse a link there.
  ⚑ A LOOP is never in tree: ``_unfollowed`` would put a looped link below *root*.
  """
  if _symlink_loop(path) or _symlink_loop(root):
    return False
  entry = _unfollowed(path) if path.is_symlink() else path.resolve()
  return root.resolve() in entry.parents


def refuse_inside_other_workset(ws: Workset, source: Path, std: StandardPaths) -> None:
  """Raise ``WorksetError`` when *source* lives inside a workset other than *ws* (no write).

  In-tree detection would shadow it and mis-resolve it, so no flag overrides this.
  """
  resolved_source = source.resolve()
  target_root = ws.root.resolve()
  for other_name, other_root in list_worksets(std).items():
    if _symlink_loop(Path(other_root)):
      continue
    other_root = Path(other_root).resolve()
    if other_root == target_root:
      continue
    try:
      resolved_source.relative_to(other_root)
    except ValueError:
      continue
    raise WorksetError(
      f"Cannot connect '{resolved_source}': it lives inside workset "
      f"'{other_name}' ({other_root}). It would be shadowed by "
      "in-tree detection and mis-resolve. Move it outside that "
      "workset, or connect it to that workset instead."
    )


def refuse_existing_box(source: Path, std: StandardPaths, *, force: bool = False) -> None:
  """Raise ``WorksetError`` when connecting *source* would absorb a standalone box or
  re-register a box another workset already connects there (no write).

  Keyspace § 0: a standalone root, by its path, a link, or a linked parent, is another
  box's.  Connecting it overwrites no file, so it is refused unless --force, and with
  --force ``add_project`` absorbs the box.  A path inside another member's workspace
  shares that tree: refused unless --force too.  The member's own path is refused
  whatever the flag.
  """
  from kanibako.launch import box_resolve

  resolved_source = source.resolve()
  # ⚑ An in-place standalone MARKER is the box's self-declaration; connecting it would
  # be a silent "steal" + dual registration.  Refused, nothing is written, so the box
  # stays standalone.
  if not force and box_resolve.is_standalone_root(resolved_source):
    via = (
      f" (a link to {resolved_source})" if Path(literal_path(source)) != resolved_source else ""
    )
    raise WorksetError(
      f"Cannot connect '{literal_path(source)}'{via}: it is a standalone box "
      "(in-place marker present). Connecting it would absorb a box "
      "that declares itself standalone. Re-run with --force to connect "
      "it anyway (it becomes a workset box), or convert it explicitly "
      "first."
    )

  existing = box_resolve.find_connected_external_box(source, std)
  if existing is not None and (
    Path(literal_path(existing.box_path)) != Path(literal_path(source))
  ):
    # Inside a member, the two boxes share its tree and no file is overwritten, so
    # --force passes it (keyspace § 0).
    if not force:
      raise WorksetError(
        f"Cannot connect '{resolved_source}': it is inside the workspace of member "
        f"'{existing.box_name}' of workset '{existing.workset_name}' "
        f"({existing.box_path}). Both boxes would claim it: re-run with --force to "
        "share it, or choose another path."
      )
  elif existing is not None:
    raise WorksetError(
      f"Cannot connect '{resolved_source}': it is already connected "
      f"as project '{existing.box_name}' in workset "
      f"'{existing.workset_name}'. Disconnect it first."
    )


def add_project(
  ws: Workset,
  name: str,
  source_path: Path,
  std: StandardPaths | None = None,
  force: bool = False,
  *,
  restoring: bool = False,
  noun: str | None = None,
  outer: _Unwind | None = None,
  outer_row: tuple[str, str] | None = None,
  rollback: _Unwind | None = None,
  leftover_cure: str = REMOVE_LEFTOVER,
  drop_marker: bool = False,
) -> WorksetProject:
  """Add a project to a workset; an EXTERNAL *source_path* (with *std*) is CONNECTED instead.

  *restoring* marks an unwind re-registering a member it just released: that creates no
  workspace, so a null ``workset.workspaces`` must not block it.  A linked vault leaf
  is kept as it stands.

  A failed unwind of this call is reported by the caller's operation: *noun* names it in
  the Note printed here, or *outer* (an enclosing unwind) takes the errors for its own.
  With neither, the caller names the failure itself.  ⚑ An *outer* removes the member's
  leaves itself (``_unwind_target_member``), so this call does not; a *restoring* call's
  outer has no such step, so there this call removes what it created.  *outer_row* is
  the standalone ``(name, root)`` row *outer* re-registers; a forced absorb of that row
  leaves its restore to *outer*, so a failed re-register is named once.  *rollback*
  only counts this call's failed undo steps, so a caller can tell a clean failure from a
  partial one.  *leftover_cure* ends the Note naming a dir this call could not remove.
  *drop_marker* (``workset connect`` only) removes a forced standalone
  source's marker as the LAST step, after the membership write, so an interrupt leaves
  the marker or the membership, never neither; the unwind writes it back.
  """
  # ⚑ Case-blind (§0): the registry write treats ``Proj`` as member ``proj`` and
  # replaces its row, orphaning ``proj``'s store.
  held = find_identifier(name, (p.name for p in ws.projects))
  if held is not None:
    raise WorksetError(f"Project '{held}' already exists in workset '{ws.name}'.")

  resolved_source = source_path.resolve()
  literal_source = Path(literal_path(source_path))

  # External ⇔ not one of the workset's own workspace dirs.
  is_external = std is not None and not is_in_tree_workspace(ws, resolved_source)
  # ⚑ An in-tree member IS a workspace under ``workset.workspaces``; a null there refuses
  # before anything is created.  An external member keeps its own dir and still connects.
  if not is_external and not restoring:
    refuse_null_workspaces(ws.root, f"a workspace for '{name}'", early=ws.early_scope)

  # ⚑ Validate up front: every EXTERNAL refusal fires BEFORE any directory is created.
  # Internal sources and std-less callers (e.g. migrate) skip this block entirely.
  if std is not None and is_external:
    refuse_inside_other_workset(ws, source_path, std)
    refuse_existing_box(literal_source, std, force=force)

  # ⚑ A --force absorb finds the box's row by its directory, under any spelling, and
  # records the member at the row's path, else the path as given.  A link LEAF is
  # resolved, the leaf only ([R188], as ``registry_store.standalone_identity_holder``):
  # when its target's real parent is the given parent's real path, the member records
  # ``<given parent>/<target name>``, so a linked ancestor stays as given; a link into
  # another directory records the full resolve.
  absorbed: tuple[str, str] | None = None
  from kanibako.launch import box_resolve as _box_resolve

  marked = (
    std is not None and is_external and force and _box_resolve.is_standalone_root(resolved_source)
  )
  if marked:
    from kanibako.project import registry_store

    assert std is not None  # implied by *marked*; mypy cannot track it
    absorbed_name = registry_store.standalone_name_for_same_dir(std.registry, literal_source)
    if absorbed_name is not None:
      absorbed = (absorbed_name, registry_store.load_standalone(std.registry)[absorbed_name])
      literal_source = Path(literal_path(absorbed[1]))
    if registry_store._leaf_is_link(str(literal_source)):
      target = literal_source.resolve()
      literal_source = (
        literal_source.parent / target.name
        if target.parent == literal_source.parent.resolve()
        else target
      )

  # ⚑⚑ THE ONE RECORDED PATH: an EXTERNAL connect records the source dir itself; an
  # in-tree member records ``workspaces/<name>``, which is the dir created below and
  # the only one it ever mounts.  Recording the caller's *source_path* for an in-tree
  # connect wrote a path the box never ran on.  Under a null ``workset.workspaces`` only
  # a *restoring* unwind reaches the in-tree arm; it re-records the member's own path.
  workspaces = ws.workspaces_dir
  recorded_workspace = literal_source if is_external or workspaces is None else workspaces / name

  # Multi-step: the external case touches a symlink + the box dirs before the
  # durable membership write.  Unwind in reverse so the connect is all-or-nothing.
  unwind = _Unwind()
  own_leaves = outer is None or restoring
  try:
    # Box dir always real.  exist_ok keeps it idempotent; unwind only rmtrees
    # what we may have created.
    proj_box = ws.projects_dir / name
    existed_box = proj_box.exists()
    proj_box.mkdir(parents=True, exist_ok=True)
    if not existed_box and own_leaves:
      unwind.push(partial(_unwind_created_root, proj_box, cure=leftover_cure))

    # Vault nests ro/rw ABOVE the box name, matching PRIMARY and STANDALONE.
    # ⚑ Unwind removes the per-box LEAVES only — never the shared ro/rw parents.
    # ⚑ The per-box leaves are composed by the one NAMED-mode accessor, off the
    # RESOLVED arms: a null arm gets no leaf, for there is no dir to nest one under.
    _shell, vault_ro_proj, vault_rw_proj = _workset_box_paths(
      proj_box,
      *resolve_workset_vault_pair(ws.root, early=ws.early_scope),
      name,
    )
    if not existed_box and not restoring:
      _warn_adopted_vault_leaves(name, (vault_ro_proj, vault_rw_proj))
    if vault_ro_proj is not None and not vault_ro_proj.is_symlink():
      existed_vault_ro = vault_ro_proj.exists()
      vault_ro_proj.mkdir(parents=True, exist_ok=True)
      if not existed_vault_ro and own_leaves:
        unwind.push(partial(_unwind_created_root, vault_ro_proj, cure=leftover_cure))
    if vault_rw_proj is not None and not vault_rw_proj.is_symlink():
      existed_vault_rw = vault_rw_proj.exists()
      vault_rw_proj.mkdir(parents=True, exist_ok=True)
      if not existed_vault_rw and own_leaves:
        unwind.push(partial(_unwind_created_root, vault_rw_proj, cure=leftover_cure))

    if is_external:
      # ⚑ workspaces/{name} is a discoverability SYMLINK — never mounted.
      # is_external implies std is not None, but mypy can't track that.
      assert std is not None
      link = ensure_discoverability_link(ws, name, literal_source)
      if link is not None and own_leaves:
        unwind.push(lambda: link.unlink() if link.is_symlink() else None)

      # ⚑ --force absorb: MOVE the registration — a box lives in EXACTLY ONE
      # registry.  ``workset connect`` also drops the marker, below; convert and
      # move rebuild the root themselves.
      if absorbed is not None:
        from kanibako.project import registry_store

        dropped_name, dropped_root = absorbed
        registry_store.unregister_standalone(std.registry, dropped_name)

        def _restore_standalone() -> None:
          registry_store.register_standalone(std.registry, dropped_name, Path(dropped_root))

        if outer_row is None or not (
          find_identifier(dropped_name, (outer_row[0],)) is not None
          and dropped_root == outer_row[1]
        ):
          unwind.push(_restore_standalone)
    else:
      # Internal (or no std): a real workspace directory.
      ws_dir = recorded_workspace
      existed_ws = ws_dir.exists()
      ws_dir.mkdir(parents=True, exist_ok=True)
      if not existed_ws and own_leaves:
        unwind.push(partial(_unwind_created_root, ws_dir, cure=leftover_cure))

    # ⚑ Durable registry write LAST, so a failure leaves no orphaned record.
    # ⚑⚑ ONE WRITE FOR EVERY MEMBER, in-tree and external alike: the P7/D10
    # ``boxes: {name → path}`` entry IS the membership record — sparse create
    # (P8b/Option A) writes no workset.yaml, and box_resolve reads this row for
    # BOTH identity and the workspace override.  Idempotent (overwrites a move).
    # ⚑⚑ The J2 ``op: connect`` bracket lives in ``workset_cmd.run_connect``, NOT
    # here — this is ALSO the membership seam for move/convert/duplicate, which
    # must not emit a ``connect`` entry.
    from kanibako.settings.paths import (
      _register_workset_box_membership,
      _workset_box_membership_undo,
    )

    unwind.push(_workset_box_membership_undo(ws.root, name, recorded_workspace))
    _register_workset_box_membership(ws.root, name, recorded_workspace)

    proj = WorksetProject(name=name, source_path=recorded_workspace)
    ws.projects.append(proj)
    unwind.push(lambda: _detach_project(ws, name))

    # ⚑ A box is in one workset only, so the forced standalone's marker goes, LAST.
    # The file stays: it holds the kuid and the user's settings.
    if drop_marker and marked:
      from kanibako.settings.paths import drop_standalone_marker

      drop_standalone_marker(resolved_source, unwind.push)
  except BaseException as exc:
    unwind.run()
    for tally in (outer, rollback):
      if tally is not None:
        tally.failed += unwind.failed
        tally.errors += unwind.errors
    if outer is None and unwind.failed and noun is not None:
      _note_unwind_incomplete(
        noun,
        f"check '{name}' in workset '{ws.name}' at {ws.root}",
        unwind.errors,
        interrupted=isinstance(exc, KeyboardInterrupt),
      )
    raise

  return proj


def refuse_case_variant_store(ws: Workset, name: str, std: StandardPaths) -> None:
  """Refuse NEW member *name* when *ws* keeps a store under a case variant of it.

  ⚑ Case-blind (§0): ``PROJ`` after a plain disconnect of ``proj`` would build a second
  store beside the kept one and orphan it.  Called by the callers that mint a member
  (connect, named create, duplicate), never by a relocation: a move may keep the
  box's own leaf on purpose.  A leaf a LIVE member of any workset owns (a shared arm)
  is that box's store, not a kept one.
  """
  bases = _member_store_bases(ws)
  found = [
    entry
    for base in bases
    if base.is_dir()
    for entry in sorted(base.iterdir())
    if entry.name != name and find_identifier(name, (entry.name,)) is not None
  ]
  if found:
    live = _live_member_stores(std, {base.resolve() for base in bases if not _symlink_loop(base)})
    found = [entry for entry in found if (entry.parent.resolve(), entry.name) not in live]
  if found:
    spellings = ", ".join(f"'{s}'" for s in dict.fromkeys(e.name for e in found))
    raise WorksetError(
      f"Project '{name}' would build a second store beside the kept store of "
      f"{spellings} in workset '{ws.name}' (names compare case-blind): "
      f"{', '.join(str(e) for e in found)}. Use the existing spelling to "
      f"re-adopt it, or remove those directories first."
    )


def _live_member_stores(std: StandardPaths, bases: set[Path]) -> set[tuple[Path, str]]:
  """``(base, member)`` for each live member of any workset that stores under *bases*."""
  live: set[tuple[Path, str]] = set()
  named: list[tuple[str, Path | None]] = [("", None), *list_worksets(std).items()]
  for ws_name, root in named:
    try:
      other = (
        default_workset(std)
        if root is None
        else load_workset(root, ws_name, early_system=std.early_system)
      )
      other_bases = _member_store_bases(other)
    except (WorksetError, ConfigError):
      continue  # unreadable: its stores stay counted, so the check refuses
    for base in other_bases:
      if not _symlink_loop(base) and base.resolve() in bases:
        live.update((base.resolve(), p.name) for p in other.projects)
  return live


def _warn_adopted_vault_leaves(name: str, leaves: Iterable[Path | None]) -> None:
  """Warn that new member *name* adopts each vault leaf in *leaves* already present.

  ⚑ Adopting is by design, so never a refusal.  Called only for a new box dir: a kept
  one means a plain disconnect kept the member's own store.
  """
  for leaf in leaves:
    if leaf is None or not (leaf.exists() or leaf.is_symlink()):
      continue
    shown = f"{leaf} -> {leaf.readlink()}" if leaf.is_symlink() else str(leaf)
    print(
      f"Warning: project '{name}' adopts an existing vault directory it did not create: {shown}",
      file=sys.stderr,
    )


def ensure_discoverability_link(ws: Workset, name: str, target: Path) -> Path | None:
  """Link ``workspaces/<name>`` → an external member's *target*; the link iff created.

  ⚑ An occupied leaf (dir, file or link) is left alone — a relocation re-runs this after
  it retires the old in-tree leaf that held the spot.  A null ``workset.workspaces`` has no
  dir to link in, so the member connects without one (Q96).
  """
  workspaces = ws.workspaces_dir
  if workspaces is None:
    return None
  workspaces.mkdir(parents=True, exist_ok=True)
  link = workspaces / name
  if link.exists() or link.is_symlink():
    return None
  link.symlink_to(target)
  return link


def _detach_project(ws: Workset, name: str) -> None:
  """Drop *name* from the in-memory project list (compensating action)."""
  ws.projects[:] = [p for p in ws.projects if p.name != name]


def _find_member(ws: Workset, name: str) -> WorksetProject:
  for p in ws.projects:
    if p.name == name:
      return p
  raise WorksetError(f"Project '{name}' not found in workset '{ws.name}'.")


def _unfollowed(path: Path) -> Path:
  """*path* with its PARENT resolved and the leaf NOT followed (a link stays a link).
  A parent on a loop cannot resolve, so it stays as given."""
  parent = path.parent
  return (parent if _symlink_loop(parent) else parent.resolve()) / path.name


def release_project(ws: Workset, name: str, *, keep_link: bool = False) -> WorksetProject:
  """Drop *name*'s membership RECORD; ⚑ never deletes a directory.

  ⚑⚑ The ONE path a relocation may take out of a workset: the member's workspace leaf is
  left exactly as it is — a real dir, or an in-tree symlink the user placed there.  Only
  an EXTERNAL member's discoverability link (recorded path ≠ ``workspaces/<name>``) is
  unlinked, and only the link.  Its store is :func:`remove_member_store`.
  *keep_link* skips that unlink (a rollback).
  """
  target = _find_member(ws, name)

  # ⚑⚑ ORDER IS THE REVERSE OF add_project: clean the link BEFORE the durable write, so
  # the registry removal is the LAST durable step and a crash mid-cleanup leaves a
  # RE-RUNNABLE state, not a locked-out external path.
  # ⚑ Under a null ``workset.workspaces`` a link made before the null still sits at the
  # DEFAULT place; unlink it there (never create anything), or a later in-tree member of
  # the same name would inherit the old external folder through it.
  link = (
    resolve_workspaces_locator(
      ws.root,
      load_workset_settings_doc(ws.root),
      early=ws.early_scope,
    )
    / name
  )
  if not keep_link and link.is_symlink() and _unfollowed(target.source_path) != _unfollowed(link):
    link.unlink()

  # ⚑⚑ Durable registry removal LAST, and UNCONDITIONAL: the ``boxes:`` row is the
  # member's ONE record, so an in-tree member must lose it too.  Dropping only
  # EXTERNAL rows orphaned an in-tree disconnect's entry, and the orphan then tripped
  # the workspace-uniqueness refusal — locking that workspace out of its own workset
  # under any name, with no way back short of hand-editing registry.yaml.
  from kanibako.settings.paths import _unregister_workset_box_membership

  _unregister_workset_box_membership(ws.root, name)
  ws.projects.remove(target)
  return target


def _member_store_bases(ws: Workset) -> tuple[Path, ...]:
  """*ws*'s resolved ``(boxes, vault_ro, vault_rw)`` — what :func:`remove_member_store` deletes under.

  ⚑ A NULL VAULT ARM CONTRIBUTES NO BASE: there is no such dir, so no per-box leaf
  under it is removed.  The boxes dir is always first.
  """
  vault_ro, vault_rw = resolve_workset_vault_pair(ws.root, early=ws.early_scope)
  return tuple(base for base in (ws.projects_dir, vault_ro, vault_rw) if base is not None)


class StoreRemovalError(OSError):
  """A member store leaf REFUSED deletion; :attr:`leaf` is the path that failed."""

  def __init__(self, message: str, leaf: Path) -> None:
    super().__init__(message)
    self.leaf = leaf


def remove_member_store(
  ws: Workset,
  name: str,
  *,
  bases: tuple[Path, ...] | None = None,
  keep: Collection[Path] = (),
) -> None:
  """Delete *name*'s box tree and per-box vault leaves; ⚑ NEVER its workspace leaf.

  *bases* is :func:`_member_store_bases`, resolved by a caller that must refuse BEFORE an
  irreversible step (``remove_project`` deletes under these bases before it releases).
  A leaf in *keep* (the caller already named it) is left in place.
  """
  # ⚑ Per-box LEAVES only, never the shared parents.  ⚑⚑ RESOLVED to match
  # ``add_project``, or a repointed vault is orphaned and an unused dir removed.
  # ⚑ EVERY removal needs the UNSHARE ESCALATION (J-7): ``rmtree`` cannot enter a 555
  # dir the caller OWNS, and a container writes as root.  ``remove_path`` spares a
  # linked leaf's target.
  from kanibako.runtime.container import remove_path

  for base in bases or _member_store_bases(ws):
    leaf = base / name
    if leaf in keep:
      continue
    if (leaf.is_dir() or leaf.is_symlink()) and not remove_path(leaf):
      # ⚑ LOUD, never a silent pass.
      err = StoreRemovalError(
        f"could not remove {leaf}; try: podman unshare rm -rf {shlex.quote(str(leaf))}", leaf
      )
      raise err


def remove_project(
  ws: Workset,
  name: str,
  *,
  remove_files: bool = False,
  std: StandardPaths | None = None,  # noqa: ARG001 - caller parity with add_project
  keep: Collection[Path] = (),
) -> WorksetProject:
  """Disconnect *name*; with *remove_files*, also delete its store AND workspace leaf.

  ⚑⚑ The one deleter of a workspace leaf, for ``workset disconnect --remove-files`` —
  a relocation composes :func:`release_project` + :func:`remove_member_store` instead.
  An external source dir is NEVER touched: its leaf is a link, and a link is unlinked.
  A path in *keep* (a store leaf, or the workspace leaf) is left in place: the caller
  found another box's recorded workspace inside it and named it.
  ⚑ *std* is accepted and unused.
  """
  # ⚑⚑ DELETE FIRST, RELEASE LAST.  Releasing first made a REFUSING store a locked
  # door: the member was already unregistered when the delete failed, so its data sat
  # unrecorded and the command could not be re-run.  Failed delete ⇒ record INTACT.
  if not remove_files:
    return release_project(ws, name)

  import shutil

  _ = ws.registry_path
  bases = _member_store_bases(ws)
  leaf = member_workspace_leaf(ws, name)  # ⚑ before any delete: it finds the member
  remove_member_store(ws, name, bases=bases, keep=keep)
  if leaf is not None and leaf.is_symlink():
    leaf.unlink()
  elif leaf is not None and leaf.is_dir() and leaf not in keep:
    shutil.rmtree(leaf)
  return release_project(ws, name)


def member_workspace_leaf(ws: Workset, name: str) -> Path | None:
  """The workspace leaf :func:`remove_project` deletes for *name*; ``None`` if none.

  ⚑ Under a null ``workset.workspaces`` there is no ``workspaces/<name>``: an in-tree
  member's leaf is the path its record holds; an external one has no leaf here.  A
  looped record is judged by where it sits, since it cannot resolve.
  """
  target = _find_member(ws, name)
  workspaces = ws.workspaces_dir
  if workspaces is not None:
    return workspaces / name
  source = target.source_path
  if _symlink_loop(source):
    parents = _unfollowed(source.absolute()).parents
    in_tree = not _symlink_loop(ws.root) and (
      ws.root.resolve() in parents or ws.root.absolute() in parents
    )
  else:
    in_tree = source_in_tree(ws, source) and source.resolve() != ws.root.resolve()
  return source if in_tree else None
