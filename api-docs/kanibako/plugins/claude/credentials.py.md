# `packages/agent-claude/src/kanibako/plugins/claude/credentials.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with the gen-api-doc tool, kept in the maintainer's canon notebook outside this repository.**


## Functions
```
def merge_oauth_in(src: Path, dst: Path) -> bool
def refresh_host_to_project(host_creds: Path, project_creds: Path) -> bool
def writeback_project_to_host(project_creds: Path) -> None
def merge_oauth_account_out(project_json: Path, host_json: Path) -> bool
```
