# `src/kanibako/launch/shells.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/launch/shells.py.md`.


## Variables

```
_STORE_SECTION = 'image_shells'
_PROBE_SCRIPT = 'u=$(id -un); getent passwd "$u" 2>/dev/null | cut -d: -f7 || grep "^$u:" /etc/passwd 2>/dev/null | cut -d: -f7'
```

## Functions
```
def load_image_shells(std) -> dict[str, str]
def save_image_shell(std, key: str, shell: str) -> None
def image_store_key(runtime, image: str) -> str
def probe_image_user_shell(runtime, image: str) -> str | None
def capture_image_shell(runtime, image: str, std) -> None
def resolve_box_shell(config, std, *, runtime=None, image=None) -> tuple[str, str]
def _store_path(std)
```
