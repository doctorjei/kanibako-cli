# `src/kanibako/git.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `scripts/gen-api-doc.py`.**


## Functions
```
def is_git_repo(path: Path) -> bool
def check_uncommitted(path: Path) -> None
def check_unpushed(path: Path) -> None
def get_metadata(path: Path) -> GitMetadata | None
```

## Classes

```
@dataclass
class GitMetadata:
    branch: str
    commit: str
    remotes: list[tuple[str, str]]
```
