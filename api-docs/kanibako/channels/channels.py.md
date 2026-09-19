# `src/kanibako/channels/channels.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**
Prose for these symbols lives in `llm-docs/kanibako/channels/channels.py.md`.


## Variables

```
WS_TOKEN_PRIMARY = '__PRIMARY__'
WS_TOKEN_STANDALONE = '__STANDALONE__'
CHAT_GENERAL_LEAF = 'general.md'
```

## Functions
```
def own_partition_dirs(std: StandardPaths, ws_token: str, box_name: str, *, ws_root: Path) -> OwnPartition
def workset_name_token(proj: ProjectPaths) -> str
def workset_root(proj: ProjectPaths, std: StandardPaths) -> Path
def has_workset_channels(proj: ProjectPaths) -> bool
def system_partition(std: StandardPaths, ws_token: str) -> SystemPartition
def workset_channel_paths(proj: ProjectPaths, std: StandardPaths) -> WorksetChannels | None
def partition_key_paths(std: StandardPaths, ws_token: str, ws_root: Path) -> WorksetPartition
def workset_partition_paths(proj: ProjectPaths, std: StandardPaths) -> WorksetPartition
def box_channel_addresses(proj: ProjectPaths, std: StandardPaths) -> BoxChannelAddresses
def _channels_repoint(workset_settings: Mapping[str, Any] | None, leaf: str) -> str | None
def _channel_key(ws_root: Path, workset_settings: Mapping[str, Any] | None, leaf: str, default: Path) -> Path
```

## Classes

```
@dataclass(frozen=True)
class SystemPartition:
    ws_token: str
    mailboxes: Path
    share: Path

@dataclass(frozen=True)
class WorksetChannels:
    root: Path
    common: Path
    chat: Path
    chat_general: Path
    chat_broadcast: Path
    share: Path

@dataclass(frozen=True)
class WorksetPartition:
    ws_token: str
    mailboxes: Path
    share_global: Path

@dataclass(frozen=True)
class BoxChannelAddresses:
    ws_token: str
    box_name: str
    inbox: Path
    share_global: Path
    share_workset: Path | None

@dataclass(frozen=True)
class OwnPartition:
    ws_token: str
    box_name: str
    mailbox: Path
    share_global: Path
```
