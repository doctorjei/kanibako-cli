# `src/kanibako/settings/bootstrap.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/settings/bootstrap.py.md`.


## Variables

```
XDG_DATA_HOME = 'XDG_DATA_HOME'
XDG_CONFIG_HOME = 'XDG_CONFIG_HOME'
XDG_RUNTIME_DIR = 'XDG_RUNTIME_DIR'
XDG_STATE_HOME = 'XDG_STATE_HOME'
XDG_CACHE_HOME = 'XDG_CACHE_HOME'
XDG_SPEC_DEFAULTS: dict[str, str] = {XDG_DATA_HOME: '.local/share', XDG_CONFIG_HOME: '.config', XDG_STATE_HOME: '.local/state', XDG_CACHE_HOME: '.cache'}
CONFIG_PATH_DEFAULTS: dict[str, str] = {'config.data': '$XDG_DATA_HOME/kanibako', 'config.settings': '@config.data/global/settings.yaml', 'config.agents': '@config.data/agents', 'config.primary_workset': '@config.data/primary_workset', 'config.registry': '@config.data/global/registry.yaml', 'config.journal': '@config.data/global/journal.yaml'}
SYSTEM_PATH_DEFAULTS: dict[str, str] = {'system.backup': '@config.data/backup', 'system.channelroot': '@config.data/channels', 'system.template': '@config.data/global/template', 'system.canon': '@config.data/global/canon', 'system.cache': '$XDG_CACHE_HOME/kanibako', 'system.runtime': '$XDG_RUNTIME_DIR/kanibako', 'system.channels.common': '@system.channelroot/common', 'system.channels.chat': '@system.channelroot/chat', 'system.channels.broadcast': '@system.channels.chat/broadcast.md', 'system.channels.mailboxes': '@system.channelroot/mailboxes', 'system.channels.share': '@system.channelroot/share'}
PROFILE_FILE = '.profile'
BASHRC_FILE = '.bashrc'
SHELL_D_FILE = '.shell.d'
IGNORE_FILE = '.gitignore'
BOXES_PATH = 'boxes'
CHANNELS_PATH = 'channels'
HOME_PATH = 'home'
KANIBAKO_PATH = 'kanibako'
LOGS_PATH = 'logs'
RO_PATH = 'ro'
RW_PATH = 'rw'
VAULT_PATH = 'vault'
WORKSPACES_PATH = 'workspaces'
WORKSPACE_PATH = 'workspace'
RUN_USER_UID_PATH = '/run/user/%d'
STANDALONE_META_DIR = 'box_data'
UNREGISTERED_MARKER = '__unregistered__'
KIND_PROJECT = 'project'
KIND_WORKSET = 'workset'
```
