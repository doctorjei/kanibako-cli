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
WARN_SA_SHADOWED_BY_PATH = "'%s' resolved to the path %s; the registered standalone box of the same " + 'name at %s is shadowed — reach it by its path.'
WARN_WS_BOX_BAD_NAME = "box name '%s' does not meet the naming rules (%s); it resolves by its " + 'path only, not by that name. Rename it.'
WARN_BOX_BAD_KUID = "Warning: invalid KUID '%s' for standalone box '%s' (invalid kuid); it " + 'still resolves; fix workset.kuid or set workset.skip_kuid_check=true to ' + 'silence this.'
WARN_BOX_KUID_HELD = "Warning: workset.kuid '%s' in %s belongs to registered box '%s' at %s; this box gets a new kuid."
WARN_BOX_NO_VAULT = "Warning: cannot find vault for box '%s' (expected at %s); a launch " + 'creates a new, empty vault there; restore the old one first, or set ' + 'box.enable_vault=false to silence.'
WARN_BOX_VAULT_UNUSABLE = "Warning: the %s vault for box '%s' at %s is %s; it launches without " + 'that vault; fix the path or set box.enable_vault=false to silence.'
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
ERR_BOX_SCALAR_NULL_REASON = "kanibako gives a null no meaning here: this key's default is a value, and a null is not a second spelling of it."
ERR_BOX_SCALAR_NULL_HEAD = '%s sets these box scalar keys to null:\n  %s\n'
ERR_BOX_SCALAR_NULL_CURE = ERR_BOX_SCALAR_NULL_REASON + " Delete those lines to use each key's " + 'default, or set the value you mean.'
ERR_BOX_SCALAR_NULL_SET_HEAD = 'the launch refuses a null at these box scalar keys, so this set is refused too:\n  %s\n'
ERR_BOX_STORE_TRAILING_REASON = 'its trailing separator means the final path segment resolved to nothing (an empty @meta.box.name leaves the box root pointing at the SHARED box store, so every box in the workset would resolve the same home)'
ERR_BOX_STORE_EMPTY_REASON = 'the box root \'meta.box.path\' derives from \'@workset.boxes\', so a settings file that sets workset.boxes to null / "" — or removes it — leaves every key rooted at the box root pointing somewhere at the filesystem root'
ERR_BOX_STORE_SET_HEAD = 'the launch refuses this value at the box store key, so this set is refused too:\n  %s\n'
ERR_CONFIG_PATH_REF_SCOPE = "%s is set to %r, which points at '@%s' — outside the system path tier. A system path value may reference only @config.* keys and the system path keys, so it cannot be read back at that stage — it is resolved before every other key. Reference one of those keys instead, or set the path you mean."
ERR_CONFIG_REF_ORDER = '%s is set to %r, which points at \'@%s\' — a %s key, which resolves after the %s keys this one belongs to. A key may reference only keys of its own set or of a set resolved before it (system-design "Ordering rule"). Reference one of those keys instead, or set the value you mean.'
ERR_PER_OWNER_SET = "nothing was written: %s = %r (%s scope, %s) would %s, because the value does not reach %s identity. %s Spell the identity into the value: %r, or set %s in each %s's own file."
PER_OWNER_SET_WORDS = {'workset': ('working-set', 'working set'), 'partition': ('partition', 'working set'), 'box': ('box', 'box'), 'agent': ('agent', 'agent')}
PER_OWNER_SHARERS = {('workset', 'none'): ('give every working set one shared path', 'Same-named boxes in different working sets, and every standalone box, would share it.'), ('workset', 'standalone'): ('give every standalone box one shared path', 'Every standalone box would share it.'), ('partition', 'none'): ('give every channel partition one shared path', 'Same-named boxes in different working sets would share it.'), ('partition', 'workset'): ('leave the channel partition unnamed', "A channel partition is keyed by its working set's name, {meta.workset.name}, not its path."), ('box', 'none'): ('give every box one shared path', 'Every box would share it.'), ('box', 'paired'): ('give same-named boxes in different working sets one shared path', 'Same-named boxes in different working sets would share it.'), ('box', 'own'): ('give the boxes of each working set one shared path', 'The boxes of each working set would share it.'), ('box', 'own+standalone'): ('give the boxes of each working set one shared path', 'The boxes of each working set, and every standalone box, would share it.'), ('agent', 'none'): ('give different agents one shared path', 'Different agents would share it.')}
ERR_PER_OWNER_READ = "%s is set to %r in %s, which would %s, because the value does not reach %s identity. %s Nothing was changed. Spell the identity into the value: kanibako system set '%s=%s'; or run kanibako system reset %s, then kanibako workset set <workset> '%s=…' in each %s. To stop running boxes meanwhile: kanibako stop --all stops every running box even when a settings file is refused; it lists them and asks first. On a first run (no kanibako.cfg yet), these commands refuse too: edit the system settings file by hand."
ERR_PER_OWNER_LAUNCH = "%s is set to %r in %s, which would %s, because the value does not reach %s identity. %s Nothing was changed. Spell the identity into the value: %r, or put it in each %s's own file%s."
PER_OWNER_SHARE_TAIL = '; to share one path on purpose, move it to %s'
ERR_WORKSET_EARLY_SET_HEAD = 'nothing was written: this value could not be read back.\n  %s'
ERR_WORKSET_SET_MAKES_STANDALONE = "a null workset.registry would make working set '%s' standalone, so nothing was written. Rerun with --force to set it anyway."
WARN_WORKSET_SET_MAKES_STANDALONE = "Warning: a null workset.registry makes working set '%s' standalone."
WARN_CONFIG_BAD_ENTRIES = '%s stores entries that are not keys (spec §0):\n  %s'
WARN_CONFIG_ILL_TYPED_ENTRIES = '%s stores values their keys refuse (spec §2a):\n  %s'
WARN_CONFIG_UNRESOLVABLE_ENTRIES = '%s stores values that do not resolve (spec §2a):\n  %s'
ERR_STORED_NON_SCALAR = 'a %s where one %s value goes'
ERR_CONFIG_BAD_ENTRIES_TAIL = '\nNothing was written. Remove those entries by editing the file, or rerun with --force to set anyway.'
ERR_CONFIG_CHAIN_BAD_ENTRY = "the edited value's own upstream chain reaches %s, a bad entry, so this set is refused too (spec §2a):\n%s\nName another upstream, or fix that entry by editing the file. --force does not set a value whose own chain is broken."
ERR_PROJECT_NO_PATH = "Project path '%s' does not exist."
ERR_PROJECT_BAD_DESIGNATION = 'Invalid box designation %r: it is neither a box name nor a path.'
ERR_PROJECT_NEW_HOME = 'Refusing to create project rooted at $HOME: this would mount the ' + 'entire home directory as the workspace.\n If you really want a ' + 'project here, use:\nkanibako create --standalone ~ --allow-home'
ERR_PROJECT_REG_HOME = 'Refusing to register $HOME as a project path: this would mount the ' + 'entire home directory as the workspace.'
ERR_PROJECT_NAME_USED = "Name '%s' is already registered"
ERR_DERIVED_BOX_NAME = "The directory name '%s' is not a valid box name: %s."
BOX_NAME_CHARSET = "box names must be ASCII letters, digits, '_', '-', or '.': container names " + 'allow nothing else'
ERR_LEAF_NOT_ASCII = 'it holds %s, which kanibako cannot spell in ASCII; ' + BOX_NAME_CHARSET
CURE_LEAF_NOT_ASCII = 'Rename or move the directory to an ASCII name.'
CURE_MOVED_LEAF_NOT_ASCII = 'Rename the directory to an ASCII name (or move it back).'
CURE_DERIVED_BOX_NAME = 'Give the box a valid name:\n  %s'
CURE_BOX_NAME_ASCII = 'Its ASCII spelling works:\n  %s'
CURE_DERIVED_DUP_DEST = 'Pick a destination directory whose name is a valid box name.'
CURE_DERIVED_FORK_NAME = 'Pick a fork name that is a valid box name.'
CURE_DERIVED_FORK_NAME_ASCII = "Pick a fork name that is a valid box name; '%s' works."
CURE_DERIVED_FORK_BOX = 'Move this box to a directory whose name is a valid box name, from the host:\n  %s'
ERR_PROJECT_PATH_IS_NAMED_BOX = 'Refusing to create a box at %s: it is already the workspace of ' + "named box '%s' in workset '%s', and one path is one project's record. " + 'Use that box, or free the path first:\n  kanibako box show %s/%s\n  kanibako workset disconnect %s %s --force'
ERR_WORKSET_NO_PROJECT = "Project '%s' not found in workset '%s'"
ERR_WS_CONNECT_PATH_IS_PRIMARY_BOX = "it is already the workspace of primary box '%s'; to make it a member of '%s' instead, convert that box (in place, or under another name), move it out of the way, or drop the box:\n  kanibako box convert %s --workset %s%s\n  kanibako box convert %s --workset %s --name <member> --move\n  kanibako box move %s <path>\n  kanibako box rm %s"
ERR_WORKSET_NO_WORKSET = 'No workset found for path: %s'
ERR_WORKSET_WS_NOT_BOX = "'%s' is a workset, not a single project box. Name a project inside it " + "(e.g. '%s/<project>') or run the command from a project workspace " + 'under that workset.'
ERR_WORKSET_NOT_IN_BOX = "Inside workset '%s' but not in a specific project workspace. Change " + 'to a project directory under %s/.'
ERR_WORKSET_MEMBER_NAME_CONFLICT = "In working set '%s' a box's name IS its member name, so '%s' and " + "'%s' are two names for one box; pass the member name alone."
ERR_WORKSET_MEMBER_NAME_TAKEN = "Project '%s' already exists in working set '%s'; member names are " + 'compared case-blind.'
ERR_WORKSET_MEMBER_NO_RECOVER = "--recover found no interrupted 'create' of '%s' in working set " + "'%s', so there is nothing to resume."
ERR_WORKSET_NULL_WORKSPACES = '%s sets workset.workspaces to null, so it has no workspace ' + 'directory and cannot hold %s.\nConnect a directory outside ' + 'it instead, delete that line to use the default, or set the ' + 'path you mean.'
ERR_STANDALONE_NULL_WORKSPACES = '%s sets workset.workspaces to null, so this standalone box has ' + 'no workspace directory and cannot hold %s.\nDelete that line ' + 'to use the default, or set the path you mean.'
ERR_NULL_WORKSPACE_BIND = "Cannot launch box '%s': %s sets workset.workspaces to null, so the " + 'box has no workspace to mount at ~/workspace.\nDelete that line ' + 'to use the default, or set the path you mean.'
BASHRC_CONTENTS = '# kanibako shell environment\n' + '[ -f /etc/bashrc ] && . /etc/bashrc\n' + 'export PS1="${KANIBAKO_PS1:-(kanibako) \\u@\\h:\\w\\$ }"\n' + '# Source user init scripts\n%s\n' % _SHELL_D_SOURCE_LINE
SHELL_D_CONTENTS = '# Source user init scripts\n%s\n' % _SHELL_D_SOURCE_LINE
PROFILE_CONTENTS = '# kanibako login profile\n' + '[ -f ~/.bashrc ] && . ~/.bashrc\n'
_SHELL_D_SOURCE_LINE = 'for _f in ~/.shell.d/*.sh; do [ -r "$_f" ] && . "$_f"; done\nunset _f'
```
