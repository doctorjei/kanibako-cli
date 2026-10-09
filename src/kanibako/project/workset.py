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
  section (``@config.registry``), mapping its NAME to its ROOT.  ⚑⚑ THAT MAPPING
  IS THE WHOLE OF A WORKSET'S IDENTITY: nothing under the workset root records a
  name, and there is no identity table in either file there.  ⚑ That is a fact
  about NAMING, not about FINDING ([R139]): a root is still found on disk by its
  four-dir skeleton (:func:`is_workset_skeleton`), and an unregistered one is
  imported under its leaf directory name — exactly as ``workset create`` already
  defaults a name it was not given.  The
  root ``registry.yaml`` carries the ``boxes:`` MEMBERSHIP only; the root
  ``workset.yaml`` carries SETTINGS ONLY, is sparse, and MAY BE ABSENT.  The
  runtime ``meta.workset.*`` keys (spec §1A) are derived at launch from the
  treewalk, never read off disk.  ⚑ 1.6.0/1.7.x kept a ``workset.meta`` identity
  table in ``workset.yaml``, and an unreleased 1.8.0 build a ``meta.workset``
  one; BOTH are RETIRED and now HARD-REFUSE — see
  :func:`refuse_retired_workset_identity`.
* **default workset** — the synthesized, never-persisted group of default-mode
  projects, rooted at ``@config.primary_workset``.
* **connected (external) box** — a member whose registered path lies OUTSIDE the
  workset root (D10).

⚑ The llm-doc carries the CORRECTED root layout; the tree that used to live here
had drifted (it named a ``worksets.yaml`` that no longer exists).
"""

from __future__ import annotations

import copy
import shlex
import sys
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from kanibako.project import registry_store, workset_registry
from kanibako.settings import bootstrap
from kanibako.settings.config_io import load_doc
from kanibako.channels.channels import WS_TOKEN_PRIMARY, WS_TOKEN_STANDALONE
from kanibako.errors import (
    ConfigError, LegacyWorksetIdentityError, ReservedWorksetNameError, WorksetError,
)
from kanibako.identifiers import find_identifier
from kanibako.project.names import register_name, unregister_name
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.messages import (
    ERR_CONFIG_NULL_PATH, ERR_NULL_WORKSPACE_BIND, ERR_STANDALONE_NULL_WORKSPACES,
    ERR_WORKSET_NULL_WORKSPACES,
)
from kanibako.settings.settings_resolve import UNSET, SettingsError
from kanibako.settings.workset_dirkeys import (
    EarlyScope, EarlySystem, _stored_repoint, early_repoint, refuse_inherited_per_owner,
    resolve_workset_dir_key,
)
from kanibako.utils import WORKSET_SEGMENT_PRIMARY, WORKSET_SEGMENT_STANDALONE, literal_path
# ⚑ FORWARD edge of a documented cycle: ``settings/paths.py`` breaks it by DEFERRING
# its ``project.workset`` imports into function bodies — do not add a module-scope
# edge back this way.
from kanibako.settings.paths import (
    StandardPaths, _workset_box_paths, box_log_files, box_logs_to_remove,
)

# ⚑⚑ EVERY NAME BELOW IS AN ALIAS, NEVER A VALUE.  The defaults themselves live in
# ``settings/bootstrap.py``, the designated path-literal file, and are materialized
# THERE AND NOWHERE ELSE; re-spelling one here made a second carrier that could drift.
# The local names stay only because the call sites read better with them.

# Default leaves for the RESOLVED workset dir keys — the spec's per-mode default
# formula ``@meta.workset.path/<leaf>``, applied ONCE per key in its resolver below
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
# @workset.boxes``).  ⚑ A REF NAME, not a leaf: nothing here may join it as a path component.
_BOXES_REF = f"workset.{BOXES_DIR_NAME}"

# ⚑ The ONE skeleton dir that names NO KEY: the keyspec declares ``workset.vault_ro``
# and ``workset.vault_rw`` (``@meta.workset.path/vault/{ro,rw}``) and no ``workset.vault``
# at all, so ``vault/`` is only their shared DEFAULT PARENT — there is nothing to resolve
# it through and it is always ``<root>/vault``.  ⚑ DELIBERATE: it keeps
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
# of the workset.yaml table; the ROUTE owns every token rule (@-refs, $XDG, ~) and
# owns the refusal.  ``workset_registry.resolve_workset_registry_path`` is a ninth
# face on the same route — do not give any of them a private expansion again.
# ---------------------------------------------------------------------------

def load_workset_settings_doc(root: Path) -> Mapping[str, Any] | None:
    """Read *root*'s workset ``workset.yaml``: ``None`` when ABSENT, refused when malformed.

    ⚑ READ LIKE EVERY OTHER SETTINGS FILE (``load_doc``), so a malformed one is refused
    in its OWN words, naming the file.  ⚑ CARVE-OUTS CATCH ``ConfigError``:
    the ancestor walk, and :func:`delete_workset`'s purge.
    """
    path = root / WORKSET_META_FILE
    if not path.is_file():
        return None
    return load_doc(path)


def resolve_workset_workspaces(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, standalone: bool = False, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.workspaces`` dir (*standalone* selects the singular default).

    ``None`` when ``workset.workspaces`` is a present ``<None>`` ([R177]): there is no
    workspaces dir, and the default leaf would be a path the user said does not exist.
    """
    repoint, where = early_repoint(workset_root, workset_settings, _WORKSPACES_LEAF, early=early)
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None,
        _STANDALONE_WORKSPACE_LEAF if standalone else _WORKSPACES_LEAF,
        key=_WORKSPACES_LEAF,
        where=where, standalone=standalone,
        workset_settings=workset_settings, early=early,
    )


def resolve_workspaces_locator(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, early: EarlyScope,
) -> Path:
    """Where a named root's in-tree members are FOUND: :func:`resolve_workset_workspaces`,
    or its default leaf under a null — for DETECTION only, never a place to create in.

    Members made before a null still sit at the default, and a lookup must still find them.
    """
    workspaces = resolve_workset_workspaces(workset_root, workset_settings, early=early)
    if workspaces is not None:
        return workspaces
    return resolve_workset_dir_key(
        workset_root, None, _WORKSPACES_LEAF, key=_WORKSPACES_LEAF, early=early,
    )


def _workspaces_null_file(
    workset_root: Path, *, early: EarlyScope,
) -> Path | None:
    """The settings file whose present ``<None>`` nulls *workset_root*'s ``workset.workspaces``."""
    repoint, where = early_repoint(
        workset_root, load_workset_settings_doc(workset_root), _WORKSPACES_LEAF, early=early,
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
    workset_root: Path, what: str, *, standalone: bool = False, early: EarlyScope,
) -> None:
    """RAISE, naming ``workset.workspaces`` and the file, when *workset_root* nulls it ([R177], Q96).

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
    workset_root: Path, workspace: Path | None, box: str, *, standalone: bool,
    early: EarlyScope,
) -> None:
    """RAISE when a box's ``meta.box.workspace`` resolves through a null ``workset.workspaces`` (Q106).

    The workspace bind is mounted at every launch (system-design § "The workspace bind"), so a
    box with no workspace cannot run.  Standalone's workspace IS ``@workset.workspaces``; a
    named member's is ``@workset.workspaces/<name>`` unless it is EXTERNAL — its recorded
    *workspace* lies outside *workset_root* and resolves through no key, so it still launches.
    Primary is not asked: its workspace is the project dir.
    """
    null_file = _workspaces_null_file(workset_root, early=early)
    if null_file is None:
        return
    if standalone or workspace is None or _path_in_tree(workspace, workset_root):
        raise WorksetError(ERR_NULL_WORKSPACE_BIND % (box, null_file))


def resolve_workset_boxes(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, standalone: bool = False, early: EarlyScope,
) -> Path:
    """Return the resolved ``workset.boxes`` dir (*standalone* selects the ``box_data`` default).

    ⚑ THE FLAG SELECTS A DEFAULT LEAF, NOTHING ELSE — the same shape as
    ``resolve_workset_workspaces``: spec §2c gives standalone
    ``@meta.workset.path/box_data`` where primary/named get ``.../boxes``.
    ⚑ DETECTION does NOT compose ``box_data/``: a standalone root is the one whose own
    ``workset.yaml`` stores the ``workset.registry`` null.  This key's DEFAULT LEAF marks
    nothing.

    🛑 A present ``<None>`` REFUSES, naming the key and the file ([R177], Q96): every box's
    home and settings live under this dir, so there is no box without it, and taking the
    default instead would put boxes where the user said there is no store.
    """
    default_leaf = _STANDALONE_BOXES_LEAF if standalone else BOXES_DIR_NAME
    repoint, where = early_repoint(workset_root, workset_settings, BOXES_DIR_NAME, early=early)
    if repoint is None:
        # ⚑ The config/system path keys' own null refusal text (``config._refuse_null_paths``).
        raise SettingsError(ERR_CONFIG_NULL_PATH % (where, f"workset.{BOXES_DIR_NAME}"))
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None, default_leaf,
        key=BOXES_DIR_NAME,
        where=where, standalone=standalone,
        workset_settings=workset_settings, early=early,
    )


def resolve_workset_logs(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, standalone: bool = False, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.logs`` dir (*standalone* takes the box-anchored default).

    ``None`` when ``workset.logs`` is a present ``<None>``: there is no logs dir, so nothing
    writes a box log and the helper-log bind is omitted (companion, "The helper-log bind").

    ⚑ STANDALONE's declared default is ``@workset.boxes`` (spec §2c): a lone box's root IS
    the boxes dir, with no name leaf.  It is a same-set ref, so the route resolves it like
    a set value, through the same ``workset.boxes`` face answer.
    """
    repoint, where = early_repoint(workset_root, workset_settings, _LOGS_LEAF, early=early)
    if repoint is None:
        return None
    if not isinstance(repoint, str):
        repoint = f"@{_BOXES_REF}" if standalone else None
    return resolve_workset_dir_key(
        workset_root, repoint, _LOGS_LEAF, key=_LOGS_LEAF, where=where, standalone=standalone,
        workset_settings=workset_settings, early=early,
    )


def resolve_workset_channelroot(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.channelroot`` — ⚑ primary/named ONLY; callers gate on mode.

    ``None`` for a present ``<None>``: no channel root, so no channel bind.
    """
    repoint, where = early_repoint(workset_root, workset_settings, "channelroot", early=early)
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None,
        _CHANNELROOT_LEAF,
        key="channelroot",
        where=where, standalone=False,
        workset_settings=workset_settings, early=early,
    )


def resolve_workset_canon(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.canon`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included.

    ``None`` for a present ``<None>``: the canon layer is SKIPPED (spec §2a).
    """
    repoint, where = early_repoint(workset_root, workset_settings, _CANON_LEAF, early=early)
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None,
        _CANON_LEAF,
        key=_CANON_LEAF,
        where=where, workset_settings=workset_settings, early=early,
    )


def resolve_workset_template(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.template`` dir — ⚑ primary/named ONLY; <None> in standalone.

    ``None`` for a present ``<None>``, and in STANDALONE (spec §2c): no template layer.
    """
    repoint, where = early_repoint(workset_root, workset_settings, _TEMPLATE_LEAF, early=early)
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None,
        _TEMPLATE_LEAF,
        key=_TEMPLATE_LEAF,
        where=where, standalone=False,
        workset_settings=workset_settings, early=early,
    )


def resolve_workset_vault_ro(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.vault_ro`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included.

    ``None`` for a present ``<None>``: no RO arm, so the bind is omitted (MIGRATION "A
    `null` setting inside a bind's source leaves the bind out…") and
    ``StandardPaths.primary_vault_ro`` is ``None``.
    """
    repoint, where = early_repoint(workset_root, workset_settings, _VAULT_RO_KEY, early=early)
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None,
        _VAULT_RO_LEAF,
        key=_VAULT_RO_KEY,
        where=where, workset_settings=workset_settings, early=early,
    )


def resolve_workset_vault_rw(
    workset_root: Path, workset_settings: Mapping[str, Any] | None,
    *, early: EarlyScope,
) -> Path | None:
    """Return the resolved ``workset.vault_rw`` dir — ⚑ UNIFORM IN EVERY MODE, standalone included.

    ``None`` for a present ``<None>``, on the terms of :func:`resolve_workset_vault_ro` —
    the two arms resolve INDEPENDENTLY, so either may carry it alone.
    """
    repoint, where = early_repoint(workset_root, workset_settings, _VAULT_RW_KEY, early=early)
    if repoint is None:
        return None
    return resolve_workset_dir_key(
        workset_root, repoint if isinstance(repoint, str) else None,
        _VAULT_RW_LEAF,
        key=_VAULT_RW_KEY,
        where=where, workset_settings=workset_settings, early=early,
    )


def resolve_workset_vault_pair(
    workset_root: Path, *, early: EarlyScope,
) -> tuple[Path | None, Path | None]:
    """The resolved ``(vault_ro, vault_rw)`` for *workset_root*, off ONE workset.yaml read.

    ⚑ The pair form exists because EVERY consumer wants both arms, and reading the file
    once per arm opens a window for the two to disagree about the same document — the
    same reason ``_workset_skeleton_dirs`` takes one read for its three resolutions.
    ⚑ Either arm may be ``None``: a workset may null one arm and not the other.
    """
    settings_doc = load_workset_settings_doc(workset_root)
    return (resolve_workset_vault_ro(workset_root, settings_doc, early=early),
            resolve_workset_vault_rw(workset_root, settings_doc, early=early))


def standalone_vault_teardown(
    root: Path, *, early: EarlyScope,
) -> tuple[list[Path], list[Path]]:
    """Split a STANDALONE box's vault into ``(removable, retained)`` for a teardown.

    ⚑⚑ A standalone box's vault IS the resolved arm — there is NO per-box leaf under it
    (``settings/paths.py::_standalone_box_paths``).  So "delete this box's vault" and
    "delete the directory ``workset.vault_ro`` names" are THE SAME ACT here, and they
    are NOT the same act in named or primary mode, where only a ``<box-name>`` leaf is
    ever removed and the arm outlives every box on it.  That difference is why this
    returns a SPLIT and not a list:

    * an arm STRICTLY BELOW *root* is kanibako's own skeleton, inside the tree the
      teardown is already clearing — it goes with the box, repointed or not;
    * an arm the user pointed OUTSIDE *root* (or AT *root*) is the USER'S STORE.  No
      verb ``rm -rf``\\ s that on their behalf: an absolute ``vault_rw: ~/store`` would
      make ``box rm --purge`` delete a directory the user merely nominated.  ⚑ Callers
      must PRINT the retained paths — the defect this replaced was not that the vault
      survived, it was that it survived SILENTLY.

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
    for arm in resolve_workset_vault_pair(root, early=early):
        # ⚑ A NULL ARM IS NO SUCH DIR: it names nothing to remove and nothing to keep.
        if arm is None:
            continue
        # ⚑ STRICT: ``arm == root`` must land in *retained*.  A ``vault_ro: .`` would
        # otherwise nominate the user's whole project directory for deletion.
        if _strictly_in_tree(arm, root):
            removable.append(_unfollowed(arm))
        else:
            retained.append(_unfollowed(arm))
    skeleton = root / _VAULT_LEAF
    if skeleton.is_symlink():
        removable.append(skeleton)
    elif skeleton.is_dir():
        arms = {arm.resolve() for arm in removable}
        leftover = [child for child in sorted(skeleton.iterdir())
                    if child.name != bootstrap.IGNORE_FILE
                    and not _holds_only_arms(child, arms)]
        if leftover:
            retained.extend(leftover)
        else:
            removable.append(skeleton)
    return removable, retained


def standalone_canon_teardown(
    root: Path, *, early: EarlyScope,
) -> tuple[Path | None, Path | None]:
    """A STANDALONE box's ``workset.canon`` tier as ``(removable, retained)`` for a teardown.

    The tier ``create`` stamps is the box's own.  Only one STRICTLY BELOW *root* is
    removable; a repoint outside it, or AT it, is the user's directory.  A null key
    retains the literal ``canon/``, as a nulled vault arm does.  A tier not on disk answers
    ``(None, None)``.  🛑 CALL THIS BEFORE UNLINKING THE ROOT ``workset.yaml``.
    """
    canon = resolve_workset_canon(root, load_workset_settings_doc(root), early=early)
    if canon is None:
        literal = root / _CANON_LEAF
        return None, (_unfollowed(literal) if literal.exists() or literal.is_symlink() else None)
    if not (canon.exists() or canon.is_symlink()):
        return None, None
    if _strictly_in_tree(canon, root):
        return _unfollowed(canon), None
    return None, _unfollowed(canon)


def report_retained_canon(canon: Path, root: Path) -> None:
    """The retained-canon Note: the tier is outside *root*, or not this box's tier."""
    why = ("not the canon tier of this box" if _strictly_in_tree(canon, root)
           else f"not strictly inside {root}")
    print(f"Note: left the canon folder at {canon} in place — {why}, "
          f"so it is yours to remove.", file=sys.stderr)


def _holds_only_arms(path: Path, arms: set[Path]) -> bool:
    """True when *path* is one of *arms* (RESOLVED), or a real dir holding only such.

    ⚑ An empty dir qualifies (nothing in it to lose); a file or a symlink that is not
    an arm does not.
    """
    if path.resolve() in arms:
        return True
    if path.is_symlink() or not path.is_dir():
        return False
    return all(_holds_only_arms(child, arms) for child in path.iterdir())


def retained_vault_reason(root: Path, vault: Path) -> str:
    """Why a :func:`standalone_vault_teardown` *retained* path stays, as a phrase.

    Outside *root* it is the user's own store; inside it is a ``vault/`` entry no
    ``workset.vault_*`` arm of the box names (e.g. one the user set to ``null``).
    """
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


def report_retained_vaults(root: Path, retained: Iterable[Path]) -> None:
    """Print the retained-vault Note for every path in *retained* that is on disk.

    ⚑⚑ *retained* HOLDS FILES: an ``is_dir()`` test before printing drops one.  It also
    holds out-of-root arms that may not exist; a dangling symlink counts as on disk.
    """
    for vault in retained:
        if not (vault.exists() or vault.is_symlink()):
            continue
        report_retained_vault(
            vault, f"it is {retained_vault_reason(root, vault)} and is yours to remove.",
        )


# ---------------------------------------------------------------------------
# Failure-consistency: a tiny LIFO unwind stack for multi-step mutations.
# ⚑ Mirrors ``commands/box/_lifecycle.py::_Unwind`` (minus on_success/finish).
# ---------------------------------------------------------------------------

class _Unwind:
    """LIFO stack of compensating actions for fail-consistent mutations."""

    def __init__(self) -> None:
        self._actions: list[Callable[[], None]] = []

    def push(self, action: Callable[[], None]) -> None:
        self._actions.append(action)

    def run(self) -> None:
        while self._actions:
            action = self._actions.pop()
            try:
                action()
            except Exception:  # noqa: BLE001 - best-effort restore
                pass


@contextmanager
def _journal_connect(
    journal: Path | None,
    box_path: Path,
    *,
    name: str,
    workset: str | None = None,
    workspace: str | None = None,
):
    """Bracket a ``connect`` register with a J2 write-ahead journal entry (no seed step)."""
    if journal is None:
        yield
        return
    from kanibako.launch import journal as journal_mod

    journal_mod.write_entry(
        journal, box_path, op="connect", name=name, mode="named",
        workset=workset, workspace=workspace,
    )
    yield
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
WORKSET_RENDERED_SEGMENTS = frozenset({
    WORKSET_SEGMENT_PRIMARY, WORKSET_SEGMENT_STANDALONE,
})

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
    ([R172]); refusing is both the safer direction and the consistent one.
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
    is_default: bool = False                 # True = synthesized default workset
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
        (``settings_launch``, ``meta.box.path | @workset.boxes/@meta.box.name``); this
        property is a FACE on that answer, never a second one.
        """
        return resolve_workset_boxes(
            self.root, load_workset_settings_doc(self.root), early=self.early_scope,
        )

    @property
    def workspaces_dir(self) -> Path | None:
        """The resolved ``workset.workspaces`` dir; ``None`` when the root nulls it (no dir, Q106)."""
        # ⚑ RESOLVED, not composed (§3.3: real and USED), and read off the root's file
        # each time, like :attr:`projects_dir`.
        return resolve_workset_workspaces(
            self.root, load_workset_settings_doc(self.root), early=self.early_scope,
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
            self.root, load_workset_settings_doc(self.root), early=self.early_scope,
        )

    @property
    def vault_rw_dir(self) -> Path | None:
        """The resolved ``workset.vault_rw`` — ⚑ RESOLVED, not composed; ``None`` when nulled."""
        return resolve_workset_vault_rw(
            self.root, load_workset_settings_doc(self.root), early=self.early_scope,
        )

    @property
    def logs_dir(self) -> Path | None:
        """The resolved ``workset.logs`` dir — ⚑ RESOLVED, not composed; primary/named ONLY.

        ``None`` when ``workset.logs`` is a present ``<None>`` (no logs dir).

        ⚑ The helper-log MOUNT has always been the spec spelling
        (``@workset.logs/@{meta.box.name}.jsonl``, ``data/rom/settings/core-defaults.yaml``), so a
        composed leaf here made the hub WRITE somewhere the box does not READ — the
        split migration M-14 records.  A standalone box's log is resolved through the
        same key with ``standalone=True``, not through this property, which is a
        WORKSET face; see ``settings/paths.py::helper_log_path``.
        """
        return resolve_workset_logs(
            self.root, load_workset_settings_doc(self.root), early=self.early_scope,
        )

    @property
    def settings_path(self) -> Path:
        """The workset-tier settings file — SETTINGS ONLY, and may not exist."""
        return self.root / WORKSET_META_FILE

    @property
    def registry_path(self) -> Path:
        """The resolved per-workset ``registry.yaml`` — the ``boxes:`` membership, and only that."""
        return workset_registry.resolve_workset_registry_path(
            self.root, load_workset_settings_doc(self.root), early=self.early_scope,
        )


# ---------------------------------------------------------------------------
# Loading a workset: its NAME comes from the caller (who read it out of the global
# registry), its MEMBERS from the root registry.yaml's ``boxes:`` section, and its
# settings from the root workset.yaml.  ⚑ Neither file under the root is read for
# identity — there is none there to read.
# ---------------------------------------------------------------------------

def _load_workset(root: Path, name: str, *, early_system: EarlySystem) -> Workset:
    """Build the :class:`Workset` for the globally-registered *name* rooted at *root*.

    A root storing the standalone ``workset.registry`` null still lists its members, from
    the default registry path: every member guard (``workset rm`` included) counts them here.
    """
    # ⚑ A root still carrying a RETIRED identity table refuses here, with the named
    # cure — it is the load path, not detection, that a 1.6/1.7 user reaches first
    # (their workset IS globally registered, so detection resolves it fine).
    refuse_reserved_registered_name(name, root, early_system=early_system)
    refuse_retired_workset_identity(root)
    settings_doc = load_workset_settings_doc(root)
    ws = Workset(name=name, root=root, early_system=early_system)
    from kanibako.launch.box_resolve import stores_standalone_registry_null
    registry_path = workset_registry.resolve_workset_registry_path(
        root, None if stores_standalone_registry_null(root) else settings_doc, early=ws.early_scope,
    )
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
            Workset(name=name, root=root, early_system=early_system).registry_path)
    except (ConfigError, WorksetError):
        boxes = {}
    in_tree = sorted(
        (box, Path(path).relative_to(root)) for box, path in boxes.items()
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
    steps += [
        f"cd {shlex.quote(str(new_root / rel))} && kanibako {verb}" for _, rel in in_tree
    ]
    if in_tree:
        tail = (f"The first '{verb}' imports the working set under its directory name; "
                + ("each one re-points that box at its new path." if moved
                   else "the others only confirm each box."))
    else:
        steps.append(f"cd {shlex.quote(str(new_root))} && kanibako box info")
        tail = ("The last command imports the working set under its directory name, "
                "then exits 1 saying you are not inside a project, which is expected.")
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
            "the top-level `meta:` table holds nothing a workset root may set, so "
            "delete the whole of it"
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
        f"automatic migration for this — see MIGRATION.md §2.43."
    )


# ---------------------------------------------------------------------------
# Global worksets registry: the ``worksets`` section of ``config.registry``.
# ⚑⚑ THE ONE PLACE A WORKSET'S NAME IS RECORDED — its identity, not an index of it.
# ⚑ ONE section in ONE file; ``register_name``/``unregister_name`` are the SOLE writers.
# ---------------------------------------------------------------------------

def _load_registry(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` from the global worksets registry.

    ⚑ An entry keyed by a ``default`` alias variant whose root is gone, or is the
    primary store itself, is DROPPED here with a note: no command could address it
    and its cure would move the primary store.  Only the registry file is written.
    """
    section = registry_store.load_section(std.registry, "worksets")
    primary = std.primary_workset.resolve()
    registry: dict[str, Path] = {}
    for name, root_str in section.items():
        root = Path(root_str)
        if find_identifier(name, RESERVED_WORKSET_IDENTIFIERS) is not None:
            if registry_store._metadata_definitively_gone(root_str):
                why = "its directory no longer exists"
            elif root.resolve() == primary:
                why = "it is the primary working set, which `default` already names"
            else:
                why = None
            if why is not None:
                if (std.registry, name) not in _UNDROPPABLE:
                    try:
                        unregister_name(std.registry, name, section="worksets", exact=True)
                    except OSError as exc:
                        _UNDROPPABLE.add((std.registry, name))
                        print(f"Warning: working set '{name}' ({root}) could not be removed "
                              f"from the registry ({why}): {exc}", file=sys.stderr)
                    else:
                        print(f"Note: removed working set '{name}' ({root}) from the "
                              f"registry: {why}. No file under it was touched.",
                              file=sys.stderr)
                continue
        registry[name] = root
    return registry


#: Droppable ``(registry, key)`` pairs whose write failed: left out in memory, warned once.
_UNDROPPABLE: set[tuple[Path, str]] = set()


def _logs_walk_targets(std: StandardPaths) -> dict[str, Path]:
    """Every workset a ``workset.logs`` share can reach: the registered ones, PLUS the
    default, which is VIRTUAL (no registry row) and owns the primary log directory."""
    return {**_load_registry(std), DEFAULT_WORKSET_ID: std.primary_workset}


def _walk_scope(system: EarlySystem, name: str, root: Path) -> EarlyScope:
    """*name*'s scope, through :attr:`Workset.early_scope` (the default reads as primary)."""
    return Workset(name, root, is_default=name == DEFAULT_WORKSET_ID,
                   early_system=system).early_scope


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
    std: StandardPaths, *, value: object, scope: str,
    target_name: str | None = None, target_root: Path | None = None,
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
    read, and paths compare RESOLVED: ``@meta.workset.path/../shared`` differs per file
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
                driven = joins or (_stored_repoint(moved, _LOGS_LEAF)
                                   != _stored_repoint(doc, _LOGS_LEAF))
                doc = moved
            elif scope == "system":
                driven = (system.tier != std.early_system.tier
                          and _stored_repoint(doc, _LOGS_LEAF) is UNSET)
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
    canonical_key: str, value: object, std: StandardPaths, *, force: bool,
    scope: str, target_name: str | None = None, target_root: Path | None = None,
) -> str | None:
    """The message a ``workset.logs`` write door prints INSTEAD of writing, or ``None``.

    *value*, *scope* and the target are :func:`find_logs_share`'s.
    """
    if force or canonical_key != "workset.logs":
        return None
    hit = find_logs_share(
        std, value=value, scope=scope, target_name=target_name, target_root=target_root,
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
    std: StandardPaths, logs_dir: Path, box: str, *, workset_root: Path | None,
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
    try:
        shared = Path(logs_dir).resolve()
    except OSError:
        return ()
    partners: list[str] = []
    for name, root in _logs_walk_targets(std).items():
        if workset_root is not None and Path(workset_root).resolve() == root.resolve():
            continue
        try:
            early = _walk_scope(std.early_system, name, root)
            doc = load_doc(root / WORKSET_META_FILE)
            resolved = resolve_workset_logs(root, doc, early=early)
        except (ConfigError, SettingsError, OSError):
            continue
        if resolved is None or Path(resolved).resolve() != shared:
            continue
        from kanibako.launch.box_resolve import stores_standalone_registry_null
        try:
            boxes = workset_registry.load_workset_boxes(
                workset_registry.resolve_workset_registry_path(
                    root, None if stores_standalone_registry_null(root) else doc,
                    early=early,
                )
            )
        except (ConfigError, SettingsError, OSError):
            partners.append(_shown(name))
            continue
        if find_identifier(box, boxes) is not None:
            partners.append(_shown(name))
    return tuple(partners)


def purge_box_logs(
    std: StandardPaths, logs_dir: Path | None, box: str, *, workset_root: Path | None,
) -> list[Path]:
    """Delete :func:`box_logs_to_purge`'s files; returns them."""
    removed = box_logs_to_purge(std, logs_dir, box, workset_root=workset_root)
    for log_file in removed:
        log_file.unlink()
    return removed


def box_logs_to_purge(
    std: StandardPaths, logs_dir: Path | None, box: str, *, workset_root: Path | None,
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
# the test cannot drift ([R139]).
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
    *"is a workset here"* — [R139]: detection and naming are two questions, and
    answering one does not answer the other.  ``_is_standalone_meta_dir`` also names
    nothing.
    ⚑ ALL are required (four, fewer under a ``<None>`` ``workset.logs`` or
    ``workset.workspaces``): any one of them alone is an ordinary directory name.
    ⚑ Three of the four are RESOLVED through their workset keys, so this finds a root
    that has repointed ``workset.boxes``, ``workset.workspaces`` or ``workset.logs``.
    """
    try:
        dirs = _workset_skeleton_dirs(root, early=early)
    except ConfigError:
        return False
    return all(subdir.is_dir() for subdir in dirs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_workset(
    name: str, root: Path, std: StandardPaths, *, force_logs_share: bool = False,
) -> Workset:
    """Create a new workset directory structure and register it globally.

    *force_logs_share* accepts a ``workset.logs`` directory another workset resolves to.
    """
    if not name:
        raise WorksetError("Workset name must not be empty.")

    if is_reserved_workset_name(name):
        raise WorksetError(
            f"Workset name '{name}' is reserved and cannot be used. The names "
            f"{', '.join(sorted(RESERVED_WORKSET_NAMES))} are reserved sentinels "
            "for the primary and standalone partitions. Choose another name."
        )

    # ⚑ Same-kind uniqueness (D-B3): refuse, never auto-suffix.  A primary box of the same
    # name is a separate namespace (spec § Detection & import), so it is not consulted.
    # ⚑ Case-blind (§0, ⚑ NAMING RULES): ``Foo`` collides with a registered ``foo``.
    registry = _load_registry(std)
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
    shared = logs_share_refusal("workset.logs", UNSET, std, force=force_logs_share,
                                scope="workset", target_name=name, target_root=root)
    if shared is not None:
        raise WorksetError(shared.removeprefix("Error: "))

    # Multi-step: disk skeleton, then the ONE global registration.  A crash between
    # them would orphan dirs, so unwind in reverse: all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # ⚑ Skeleton = the FOUR dirs of :func:`_workset_skeleton_dirs`, which is also
        # what detection tests for; auth/channels are created lazily elsewhere.  NO file
        # is written at all — no workset.yaml (a workset root need not have one) and
        # no registry.yaml (a workset with no members has no membership to record).
        root.mkdir(parents=True)
        unwind.push(lambda: shutil.rmtree(root, ignore_errors=True))
        ws = Workset(name=name, root=root, early_system=std.early_system)
        # ⚑ *root* was just created empty, so it has no workset.yaml; a resolved leaf
        # differs from its default only through the system file.  A leaf outside *root*
        # (or at it) is the user's directory, never created here; the rest lie under
        # *root*, so ``parents`` and ``exist_ok`` can only fill in this root: a system
        # value may nest a leaf or name one twice.
        for subdir_path in _workset_skeleton_dirs(root, early=ws.early_scope):
            if _path_in_tree(subdir_path, root) and subdir_path.resolve() != root.resolve():
                subdir_path.mkdir(parents=True, exist_ok=True)

        # ⚑⚑ THE REGISTRATION IS THE CREATION: this line is what makes the directory a
        # workset, because the name→root entry it writes IS the workset's identity.
        # ⚑ ONE section serves BOTH name lookup AND discovery/list — hence one call.
        register_name(std.registry, name, str(root), section="worksets")

        def _drop_workset() -> None:
            unregister_name(std.registry, name, section="worksets")

        unwind.push(_drop_workset)
    except Exception:
        unwind.run()
        raise

    return ws


def load_workset(root: Path, name: str, *, early_system: EarlySystem) -> Workset:
    """Load the workset registered as *name* at *root* (raises ``WorksetError`` if absent).

    ⚑ *name* is REQUIRED and comes from the global registry's ``worksets:`` section —
    the caller reached *root* through that mapping, and it is the only record of the
    name there is.
    """
    root = Path(literal_path(root))
    if not root.is_dir():
        raise WorksetError(f"Workset root does not exist: {root}")
    return _load_workset(root, name, early_system=early_system)


def list_worksets(std: StandardPaths) -> dict[str, Path]:
    """Return ``{name: root_path}`` for all registered worksets — ⚑ the default workset is NOT here."""
    return _load_registry(std)


def default_workset(std: StandardPaths) -> Workset:
    """Synthesize the default workset — ⚑ VIRTUAL: no registry write, no identity on disk."""
    from kanibako.settings.paths import BoxMode, _early_scope, load_primary_boxes

    projects_map = load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary))
    projects = [
        WorksetProject(name=name, source_path=Path(path))
        for name, path in projects_map.items()
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
    registry = _load_registry(std)
    # ⚑ Resolve THROUGH the stored spelling: the Workset must carry the name as
    # registered, not as typed, or everything derived from it disagrees with the registry.
    stored = find_identifier(name, registry)
    if stored is None:
        raise WorksetError(f"Working set '{name}' is not registered.")
    return load_workset(registry[stored], stored, early_system=std.early_system)


def delete_workset(name: str, std: StandardPaths, *, remove_files: bool = False) -> Path:
    """Unregister a workset and optionally remove its tree; returns the deleted root path."""
    registry = _load_registry(std)
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

    # Drop the ONE ``worksets`` entry, by the STORED spelling.  Idempotent: a missing
    # entry is a no-op.
    unregister_name(std.registry, stored, section="worksets")

    # ⚑ Irreversible step LAST: only after the registry is clean.
    if boxes_dir is not None:
        import shutil

        # ⚑⚑ BOX TREES FIRST (J-7): a whole-root rmtree hits the root-owned 555 canon
        # skeleton and leaves the workset half-deleted AFTER its registry entry is gone.
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
        if (boxes_dir.is_dir() and not boxes_dir.is_symlink()
                and _strictly_in_tree(boxes_dir, root)):
            for box_tree in sorted(boxes_dir.iterdir()):
                if box_tree.is_dir() and not box_tree.is_symlink():
                    remove_box_tree(box_tree)
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
    """
    roots = [ws.root]
    workspaces = ws.workspaces_dir
    if workspaces is not None:
        roots.append(workspaces)
    for root in roots:
        if _path_in_tree(path, root) or _path_in_tree(path.parent, root):
            return True
    return False


def _path_in_tree(path: Path, root: Path) -> bool:
    """True when *path* lies under *root*, both resolved (the in-tree test)."""
    return path.resolve().is_relative_to(root.resolve())


def _strictly_in_tree(path: Path, root: Path) -> bool:
    """True when the entry *path* names sits STRICTLY below *root*.

    A link AT *path* is judged where it sits, anything else resolved: a linked parent
    leading out of *root* puts *path* outside.  A caller that descends into *path* must
    also refuse a link there.
    """
    entry = _unfollowed(path) if path.is_symlink() else path.resolve()
    return root.resolve() in entry.parents


def refuse_existing_box(source: Path, std: StandardPaths, *, force: bool = False) -> None:
    """Raise ``WorksetError`` when connecting *source* would absorb a standalone box or
    re-register a box another workset already connects there (no write)."""
    from kanibako.launch import box_resolve

    resolved_source = source.resolve()
    # ⚑ D3-mode #1: an in-place standalone MARKER is the box's authoritative
    # self-declaration; connecting it would be a silent "steal" + dual registration.
    # The guard ALONE fixes it — with no ``boxes:`` entry, resolution finds the marker.
    if not force and box_resolve.stores_standalone_registry_null(resolved_source):
        raise WorksetError(
            f"Cannot connect '{resolved_source}': it is a standalone box "
            "(in-place marker present). Connecting it would absorb a box "
            "that declares itself standalone. Re-run with --force to connect "
            "it anyway (it becomes a workset box), or convert it explicitly "
            "first."
        )

    existing = box_resolve.find_connected_external_box(source, std)
    if existing is not None:
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
) -> WorksetProject:
    """Add a project to a workset; an EXTERNAL *source_path* (with *std*) is CONNECTED instead.

    *restoring* marks an unwind re-registering a member it just released: that creates no
    workspace, so a null ``workset.workspaces`` must not block it.
    """
    for p in ws.projects:
        if p.name == name:
            raise WorksetError(
                f"Project '{name}' already exists in workset '{ws.name}'."
            )

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
        target_root = ws.root.resolve()
        for other_name, other_root in _load_registry(std).items():
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

        refuse_existing_box(literal_source, std, force=force)

    # ⚑⚑ THE ONE RECORDED PATH: an EXTERNAL connect records the source dir itself; an
    # in-tree member records ``workspaces/<name>``, which is the dir created below and
    # the only one it ever mounts.  Recording the caller's *source_path* for an in-tree
    # connect wrote a path the box never ran on.  Under a null ``workset.workspaces`` only
    # a *restoring* unwind reaches the in-tree arm; it re-records the member's own path.
    workspaces = ws.workspaces_dir
    recorded_workspace = (literal_source if is_external or workspaces is None
                          else workspaces / name)

    # Multi-step: the external case touches a symlink + the box dirs before the
    # durable membership write.  Unwind in reverse so the connect is all-or-nothing.
    import shutil

    unwind = _Unwind()
    try:
        # Box dir always real.  exist_ok keeps it idempotent; unwind only rmtrees
        # what we may have created.
        proj_box = ws.projects_dir / name
        existed_box = proj_box.exists()
        proj_box.mkdir(parents=True, exist_ok=True)
        if not existed_box:
            unwind.push(lambda: shutil.rmtree(proj_box, ignore_errors=True))

        # Vault nests ro/rw ABOVE the box name, matching PRIMARY and STANDALONE.
        # ⚑ Unwind removes the per-box LEAVES only — never the shared ro/rw parents.
        # ⚑ The per-box leaves are composed by the one NAMED-mode accessor, off the
        # RESOLVED arms: a null arm gets no leaf, for there is no dir to nest one under.
        _shell, vault_ro_proj, vault_rw_proj = _workset_box_paths(
            proj_box, *resolve_workset_vault_pair(ws.root, early=ws.early_scope), name,
        )
        if vault_ro_proj is not None:
            existed_vault_ro = vault_ro_proj.exists()
            vault_ro_proj.mkdir(parents=True, exist_ok=True)
            if not existed_vault_ro:
                unwind.push(lambda: shutil.rmtree(vault_ro_proj, ignore_errors=True))
        if vault_rw_proj is not None:
            existed_vault_rw = vault_rw_proj.exists()
            vault_rw_proj.mkdir(parents=True, exist_ok=True)
            if not existed_vault_rw:
                unwind.push(lambda: shutil.rmtree(vault_rw_proj, ignore_errors=True))

        if is_external:
            # ⚑ workspaces/{name} is a discoverability SYMLINK — never mounted.
            # is_external implies std is not None, but mypy can't track that.
            assert std is not None
            link = ensure_discoverability_link(ws, name, literal_source)
            if link is not None:
                unwind.push(
                    lambda: link.unlink() if link.is_symlink() else None
                )

            # ⚑ --force absorb: MOVE the registration — a box lives in EXACTLY ONE
            # registry.  The in-place marker STAYS (intrinsic identity), so after a
            # disconnect the box is standalone again, but unregistered.
            from kanibako.launch import box_resolve as _box_resolve

            if force and _box_resolve.stores_standalone_registry_null(
                resolved_source
            ):
                from kanibako.project import registry_store

                std_name = registry_store.standalone_name_for_root(
                    std.registry, resolved_source
                )
                if std_name is not None:
                    dropped_name: str = std_name
                    dropped_root = registry_store.load_standalone(std.registry)[
                        dropped_name
                    ]
                    registry_store.unregister_standalone(
                        std.registry, dropped_name
                    )

                    def _restore_standalone() -> None:
                        registry_store.register_standalone(
                            std.registry, dropped_name, Path(dropped_root)
                        )

                    unwind.push(_restore_standalone)
        else:
            # Internal (or no std): a real workspace directory.
            ws_dir = recorded_workspace
            existed_ws = ws_dir.exists()
            ws_dir.mkdir(parents=True, exist_ok=True)
            if not existed_ws:
                unwind.push(lambda: shutil.rmtree(ws_dir, ignore_errors=True))

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
            _unregister_workset_box_membership,
        )

        _register_workset_box_membership(ws.root, name, recorded_workspace, early=ws.early_scope)
        unwind.push(lambda: _unregister_workset_box_membership(ws.root, name, early=ws.early_scope))

        proj = WorksetProject(name=name, source_path=recorded_workspace)
        ws.projects.append(proj)
        unwind.push(lambda: _detach_project(ws, name))
    except Exception:
        unwind.run()
        raise

    return proj


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
    """*path* with its PARENT resolved and the leaf NOT followed (a link stays a link)."""
    return path.parent.resolve() / path.name


def release_project(ws: Workset, name: str, *, keep_link: bool = False) -> WorksetProject:
    """Drop *name*'s membership RECORD; ⚑ never deletes a directory.

    ⚑⚑ The ONE path a relocation may take out of a workset: the member's workspace leaf is
    left exactly as it is — a real dir, or an in-tree symlink the user placed there.  Only
    an EXTERNAL member's discoverability link (recorded path ≠ ``workspaces/<name>``) is
    unlinked, and only the link.  Its store is :func:`remove_member_store`.
    *keep_link* skips that unlink (a rollback).
    A null ``workset.registry`` refuses (resolving :attr:`Workset.registry_path`) before any change.
    """
    _ = ws.registry_path
    target = _find_member(ws, name)

    # ⚑⚑ ORDER IS THE REVERSE OF add_project: clean the link BEFORE the durable write, so
    # the registry removal is the LAST durable step and a crash mid-cleanup leaves a
    # RE-RUNNABLE state, not a locked-out external path.
    # ⚑ Under a null ``workset.workspaces`` a link made before the null still sits at the
    # DEFAULT place; unlink it there (never create anything), or a later in-tree member of
    # the same name would inherit the old external folder through it.
    link = resolve_workspaces_locator(
        ws.root, load_workset_settings_doc(ws.root), early=ws.early_scope,
    ) / name
    if (not keep_link and link.is_symlink()
            and _unfollowed(target.source_path) != _unfollowed(link)):
        link.unlink()

    # ⚑⚑ Durable registry removal LAST, and UNCONDITIONAL: the ``boxes:`` row is the
    # member's ONE record, so an in-tree member must lose it too.  Dropping only
    # EXTERNAL rows orphaned an in-tree disconnect's entry, and the orphan then tripped
    # the workspace-uniqueness refusal — locking that workspace out of its own workset
    # under any name, with no way back short of hand-editing registry.yaml.
    from kanibako.settings.paths import _unregister_workset_box_membership

    _unregister_workset_box_membership(ws.root, name, early=ws.early_scope)
    ws.projects.remove(target)
    return target


def _member_store_bases(ws: Workset) -> tuple[Path, ...]:
    """*ws*'s resolved ``(boxes, vault_ro, vault_rw)`` — what :func:`remove_member_store` deletes under.

    ⚑ A NULL VAULT ARM CONTRIBUTES NO BASE: there is no such dir, so no per-box leaf
    under it is removed.  The boxes dir is always first.
    """
    vault_ro, vault_rw = resolve_workset_vault_pair(ws.root, early=ws.early_scope)
    return tuple(base for base in (ws.projects_dir, vault_ro, vault_rw) if base is not None)


def remove_member_store(
    ws: Workset, name: str, *, bases: tuple[Path, ...] | None = None,
) -> None:
    """Delete *name*'s box tree and per-box vault leaves; ⚑ NEVER its workspace leaf.

    *bases* is :func:`_member_store_bases`, resolved by a caller that must refuse BEFORE an
    irreversible step (:func:`remove_project` releases the member first).
    """
    # ⚑ Per-box vault LEAVES only — never the shared ro/rw parents.
    # ⚑⚑ RESOLVED, and it MUST match ``add_project``: deleting the composed default
    # while the box's real vault sits at the repoint leaves the user's data orphaned
    # AND removes a directory the box never used.
    boxes_dir, *vault_bases = bases or _member_store_bases(ws)
    # ⚑ EVERY removal here needs the UNSHARE ESCALATION (J-7): a plain ``rmtree`` cannot
    # enter a 555 dir the caller OWNS, and a container writes vault content as root.
    # ``remove_path`` also spares a linked leaf's target.
    from kanibako.runtime.container import remove_box_tree, remove_path

    box_tree = boxes_dir / name
    if box_tree.is_symlink():
        box_tree.unlink()
    elif box_tree.is_dir():
        remove_box_tree(box_tree)
    for base in vault_bases:
        leaf = base / name
        if (leaf.is_dir() or leaf.is_symlink()) and not remove_path(leaf):
            # ⚑ LOUD, never a silent pass: the relocation retire prints its leftover Note
            # with rc unchanged, and a disconnect exits 1.
            raise OSError(
                f"could not remove {leaf}; try: podman unshare rm -rf "
                f"{shlex.quote(str(leaf))}")


def remove_project(
    ws: Workset, name: str, *, remove_files: bool = False,
    std: StandardPaths | None = None,  # noqa: ARG001 - caller parity with add_project
) -> WorksetProject:
    """Disconnect *name*; with *remove_files*, also delete its store AND workspace leaf.

    ⚑⚑ The one deleter of a workspace leaf, for ``workset disconnect --remove-files`` —
    a relocation composes :func:`release_project` + :func:`remove_member_store` instead.
    An external source dir is NEVER touched: its leaf is a link, and a link is unlinked.
    ⚑ *std* is accepted and unused.
    """
    # ⚑ Resolved BEFORE the release, so a store that refuses (a null ``workset.boxes``)
    # stops the disconnect while the member is still registered.
    bases = _member_store_bases(ws) if remove_files else None
    target = release_project(ws, name)
    if bases is not None:
        import shutil

        remove_member_store(ws, name, bases=bases)
        # ⚑ Under a null ``workset.workspaces`` there is no ``workspaces/<name>``: an in-tree
        # member's leaf is the path its record holds; an external one has no leaf here.
        workspaces = ws.workspaces_dir
        if workspaces is not None:
            leaf: Path | None = workspaces / name
        else:
            in_tree = (source_in_tree(ws, target.source_path)
                       and target.source_path.resolve() != ws.root.resolve())
            leaf = target.source_path if in_tree else None
        if leaf is not None and leaf.is_symlink():
            leaf.unlink()
        elif leaf is not None and leaf.is_dir():
            shutil.rmtree(leaf)
    return target
