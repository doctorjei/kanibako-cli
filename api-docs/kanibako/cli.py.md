# `src/kanibako/cli.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/cli.py.md`.


## Variables

```
_SUBCOMMANDS = {'start', 'stop', 'shell', 'code', 'ps', 'list', 'create', 'rm', 'register', 'box', 'rig', 'workset', 'agent', 'system', 'baseline', 'setup'}
```

## Functions
```
def build_parser() -> argparse.ArgumentParser
def main(argv: list[str] | None=None) -> None
def _normalize_command(effective: list[str]) -> list[str]
def _ensure_initialized() -> None
def _setup_nudge(args: argparse.Namespace) -> None
```

## Classes

```
class _Formatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog: str, **kwargs: Any) -> None
```
