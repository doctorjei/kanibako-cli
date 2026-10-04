# `src/kanibako/settings/messages.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/messages.py.md`.


## Variables

```
STATUS_OK = 'ok'
STATUS_MISSING = 'missing'
STATUS_NO_DATA = 'no-data'
MSG_OTS_KB_INIT = '[One Time Setup] Initializing kanibako in %s... '
MSG_OTS_WS_PROJ_INIT = '[One Time Setup] Initializing workset project in %s... '
MSG_DONE = 'done.'
WARN_RELATIVE_XDG = '%s=%r is relative (not absolute); ignoring per XDG spec & using default.'
WARN_FALLBACK_RT_DIR = '%s not set; falling back to %s for runtime files ' + '(helper sockets). Set %s to a per-user runtime dir to silence this.'
WARN_RUNDIR_UNUSABLE = '%s not set & ' + RUN_USER_UID_PATH + ' unusable; falling back to temp ' + 'dir %s for runtime files. Set %s to persistent per-user runtime dir to ' + 'silence this.'
WARN_WS_NO_ROOT = "Warning: workset '%s' root missing: %s"
WARN_WS_BAD_LOAD = "Warning: failed to load workset '%s': %s"
WARN_WS_BOX_BAD_NAME = "box name '%s' does not meet the naming rules (%s); it resolves by its " + 'path only, not by that name. Rename it.'
WARN_BOX_BAD_KUID = "Warning: invalid KUID '%s' for standalone box '%s' (invalid kuid); it " + 'still resolves; fix workset.kuid or set workset.skip_kuid_check=true to ' + 'silence this.'
WARN_BOX_NO_VAULT = "Warning: cannot find vault for box '%s' (expected at %s); it still " + 'launches without a vault; recreate the directory or set ' + 'box.enable_vault=false to silence.'
ERR_SETTINGS_BAD_PATH = 'Unresolvable %s path: %s'
ERR_SETTINGS_BAD_REF = 'Unknown @%s-reference: %s'
ERR_CONFIG_NO_FILE = '%s is missing. Run any kanibako command to initialize.'
ERR_CONFIG_LAYER1_SETTINGS = '%s carries settings, which it cannot hold:\n  %s\n' + 'That file holds the config.* bootstrap paths and nothing else. ' + 'Delete those lines from it, then set what you meant with ' + "'kanibako system set <key>=<value>', which writes the settings file."
ERR_CONFIG_LAYER1_UNDECLARED = '%s carries config keys that do not exist:\n  %s\n' + 'Layer 1 declares exactly these (spec §1): %s. Fix or delete ' + 'those lines; config.* keys are only ever set by editing that file.'
ERR_CONFIG_LAYER1_TABLE = "%s has a 'config:' entry that is not a table: %s\n" + 'It carries the config.* bootstrap paths as keys under it, e.g.\n' + '  config:\n    data: /path/to/kanibako'
ERR_CONFIG_NULL_PATH_REASON = 'kanibako gives a null path key no meaning.'
ERR_CONFIG_NULL_PATH_HEAD = '%s sets these path keys to null:\n  %s\n'
ERR_CONFIG_NULL_PATH_CURE = ERR_CONFIG_NULL_PATH_REASON + " Delete those lines to use each key's " + 'default, or set the path you mean.'
ERR_CONFIG_NULL_PATH_SET_HEAD = 'the launch refuses a null at these path keys, so this set is refused too:\n  %s\n'
ERR_CONFIG_NULL_PATH = ERR_CONFIG_NULL_PATH_HEAD + ERR_CONFIG_NULL_PATH_CURE
ERR_BOX_STORE_TRAILING_REASON = 'its trailing separator means the final path segment resolved to nothing (an empty @meta.box.name leaves the box root pointing at the SHARED box store, so every box in the workset would resolve the same home)'
ERR_BOX_STORE_EMPTY_REASON = 'the box root \'meta.box.path\' derives from \'@workset.boxes\', so a settings file that sets workset.boxes to null / "" — or removes it — leaves every key rooted at the box root pointing somewhere at the filesystem root'
ERR_BOX_STORE_SET_HEAD = 'the launch refuses this value at the box store key, so this set is refused too:\n  %s\n'
ERR_CONFIG_PATH_REF_SCOPE = "%s is set to %r, which points at '@%s' — outside the system path tier. A system path value may reference only @config.* keys and the system path keys, so a launch could not read it back (spec §0: no @-ref points downward). Reference one of those keys instead, or set the path you mean."
ERR_CONFIG_DOWNWARD_REF = "%s is set to %r, which points at '@%s' — DOWNWARD, into the %s.* scope this key CONTAINS. A key may reference its own scope or one that CONTAINS it, never a scope it contains: one value would then be read the same way by every scope below it (spec §0: no @-ref points DOWNWARD). Reference a key of the containing scope instead, or set the value you mean."
WARN_CONFIG_BAD_ENTRIES = '%s stores entries that are not keys (spec §0):\n  %s'
ERR_CONFIG_BAD_ENTRIES_TAIL = '\nNothing was written. Remove those entries by editing the file, or rerun with --force to set anyway.'
ERR_CONFIG_CHAIN_BAD_ENTRY = "the edited value's own upstream chain reaches %s, which is not a key, so this set is refused too (spec §2a):\n%s\nName an upstream that is a key, or remove that entry by editing the file. --force does not set a value whose own chain is broken."
ERR_PROJECT_NO_PATH = "Project path '%s' does not exist."
ERR_PROJECT_BAD_DESIGNATION = 'Invalid box designation %r: it is neither a box name nor a path.'
ERR_PROJECT_NEW_HOME = 'Refusing to create project rooted at $HOME: this would mount the ' + 'entire home directory as the workspace.\n If you really want a ' + 'project here, use:\nkanibako create --standalone ~ --allow-home'
ERR_PROJECT_REG_HOME = 'Refusing to register $HOME as a project path: this would mount the ' + 'entire home directory as the workspace.'
ERR_PROJECT_NAME_USED = "Name '%s' is already registered"
ERR_PROJECT_DIR_IS_WS = "Name '%s' is already in use by a workset. Box and workset names are " + 'separate namespaces, but this bare name would then resolve to the ' + 'box, shadowing the workset in bare-name lookups. Re-run with --force ' + 'to create the box under this name anyway.'
ERR_WORKSET_NO_PROJECT = "Project '%s' not found in workset '%s'"
ERR_WORKSET_NO_WORKSET = 'No workset found for path: %s'
ERR_WORKSET_WS_NOT_BOX = "'%s' is a workset, not a single project box. Name a project inside it " + "(e.g. '%s/<project>') or run the command from a project workspace " + 'under that workset.'
ERR_WORKSET_NOT_IN_BOX = "Inside workset '%s' but not in a specific project workspace. Change " + 'to a project directory under %s/.'
ERR_WORKSET_NULL_WORKSPACES = '%s sets workset.workspaces to null, so it has no workspace ' + 'directory and cannot hold %s.\nConnect a directory outside ' + 'it instead, delete that line to use the default, or set the ' + 'path you mean.'
ERR_STANDALONE_NULL_WORKSPACES = '%s sets workset.workspaces to null, so this standalone box has ' + 'no workspace directory and cannot hold %s.\nDelete that line ' + 'to use the default, or set the path you mean.'
ERR_NULL_WORKSPACE_BIND = "Cannot launch box '%s': %s sets workset.workspaces to null, so the " + 'box has no workspace to mount at ~/workspace.\nDelete that line ' + 'to use the default, or set the path you mean.'
BASHRC_CONTENTS = '# kanibako shell environment\n' + '[ -f /etc/bashrc ] && . /etc/bashrc\n' + 'export PS1="${KANIBAKO_PS1:-(kanibako) \\u@\\h:\\w\\$ }"\n' + '# Source user init scripts\n%s\n' % _SHELL_D_SOURCE_LINE
SHELL_D_CONTENTS = '# Source user init scripts\n%s\n' % _SHELL_D_SOURCE_LINE
PROFILE_CONTENTS = '# kanibako login profile\n' + '[ -f ~/.bashrc ] && . ~/.bashrc\n'
_SHELL_D_SOURCE_LINE = 'for _f in ~/.shell.d/*.sh; do [ -r "$_f" ] && . "$_f"; done\nunset _f'
```
