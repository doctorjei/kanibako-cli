# `src/kanibako/channels/channels.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/channels/channels.py.md`.


## Variables

```
WS_TOKEN_PRIMARY = '__PRIMARY__'
WS_TOKEN_STANDALONE = '__STANDALONE__'
```

## Functions
```
def own_partition_dirs(std: StandardPaths, ws_token: str, box_name: str) -> OwnPartition
def workset_name_token(proj: ProjectPaths) -> str
def workset_root(proj: ProjectPaths, std: StandardPaths) -> Path
def has_workset_channels(proj: ProjectPaths) -> bool
def system_partition(std: StandardPaths, ws_token: str) -> SystemPartition
def workset_channel_paths(proj: ProjectPaths, std: StandardPaths) -> WorksetChannels | None
def box_channel_addresses(proj: ProjectPaths, std: StandardPaths) -> BoxChannelAddresses
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
