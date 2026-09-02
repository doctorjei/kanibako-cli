# `src/kanibako/settings/messages.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
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
WARN_WS_BOX_BAD_NAME = "box name '%s' does not meet the naming rules (%s); it still resolves, " + 'but rename it when convenient.'
WARN_BOX_BAD_KUID = "Warning: invalid KUID '%s' for standalone box '%s' (invalid kuid); it " + 'still resolves; fix workset.kuid or set workset.skip_kuid_check=true to ' + 'silence this.'
WARN_BOX_NO_VAULT = "Warning: cannot find vault for box '%s' (expected at %s); it still " + 'launches without a vault; recreate the directory or set ' + 'box.enable_vault=false to silence.'
ERR_SETTINGS_BAD_PATH = 'Unresolvable %s path: %s'
ERR_SETTINGS_BAD_REF = 'Unknown @%s-reference: %s'
ERR_CONFIG_NO_FILE = '%s is missing. Run any kanibako command to initialize.'
ERR_CONFIG_LAYER1_SETTINGS = '%s carries settings, which it cannot hold:\n  %s\n' + 'That file holds the config.* bootstrap paths and nothing else. ' + 'Delete those lines from it, then set what you meant with ' + "'kanibako system set <key>=<value>', which writes the settings file."
ERR_PROJECT_NO_PATH = "Project path '%s' does not exist."
ERR_PROJECT_NEW_HOME = 'Refusing to create project rooted at $HOME: this would mount the ' + 'entire home directory as the workspace.\n If you really want a ' + 'project here, use:\nkanibako create --standalone ~ --allow-home'
ERR_PROJECT_REG_HOME = 'Refusing to register $HOME as a project path: this would mount the ' + 'entire home directory as the workspace.'
ERR_PROJECT_NAME_USED = "Name '%s' is already registered"
ERR_PROJECT_DIR_IS_WS = "Name '%s' is already in use by a workset. Box and workset names are " + 'separate namespaces, but this bare name would then resolve to the ' + 'box, shadowing the workset in bare-name lookups. Re-run with --force ' + 'to create the box under this name anyway.'
ERR_WORKSET_NO_PROJECT = "Project '%s' not found in workset '%s'"
ERR_WORKSET_NO_WORKSET = 'No workset found for path: %s'
ERR_WORKSET_WS_NOT_BOX = '%s is a workset, not a single project box. Name a project inside it ' + "(e.g. '%s/<project>') or run the command from a project workspace " + 'under that workset.'
ERR_WORKSET_NOT_IN_BOX = "Inside workset '%s' but not in a specific project workspace. Change " + 'to a project directory under %s/.'
BASHRC_CONTENTS = '# kanibako shell environment\n' + '[ -f /etc/bashrc ] && . /etc/bashrc\n' + 'export PS1="${KANIBAKO_PS1:-(kanibako) \\u@\\h:\\w\\$ }"\n' + '# Source user init scripts\n%s\n' % _SHELL_D_SOURCE_LINE
SHELL_D_CONTENTS = '# Source user init scripts\n%s\n' % _SHELL_D_SOURCE_LINE
PROFILE_CONTENTS = '# kanibako login profile\n' + '[ -f ~/.bashrc ] && . ~/.bashrc\n'
_SHELL_D_SOURCE_LINE = 'for _f in ~/.shell.d/*.sh; do [ -r "$_f" ] && . "$_f"; done\nunset _f'
```
