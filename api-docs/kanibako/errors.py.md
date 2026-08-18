# `src/kanibako/errors.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/errors.py.md`.


## Functions
```
class KanibakoError(Exception):
    ...

class ConfigError(KanibakoError):
    ...

class CategoryCollisionError(ConfigError):
    def __init__(self, message: str, *, kind: str, box_dest: str, entries: 'tuple[tuple[str, str | None], ...]'=()) -> None

class TemplateScopeError(ConfigError):
    ...

class ProjectError(KanibakoError):
    ...

class ContainerError(KanibakoError):
    ...

class ArchiveError(KanibakoError):
    ...

class GitError(KanibakoError):
    ...

class WorksetError(KanibakoError):
    ...

class UserCancelled(KanibakoError):
    ...

class SubjectConflictError(KanibakoError):
    ...

class AgentResolutionError(KanibakoError):
    ...

class NoAgentSelectedError(AgentResolutionError):
    ...

class NoAgentInstalledError(AgentResolutionError):
    ...

class AgentNotInstalledError(AgentResolutionError):
    ...
```
