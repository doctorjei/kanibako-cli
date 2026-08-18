# `src/kanibako/commands/setup_cmd.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/setup_cmd.py.md`.


## Functions
```
def add_arguments(parser: argparse.ArgumentParser) -> None
def run_setup(args: argparse.Namespace) -> int
def _detected_agents() -> list[tuple[str, str]]
def _known_target_names() -> list[str]
def _settings_paths() -> tuple[Path, Path]
def _write_system_agent(name: str) -> None
def _write_setup_marker() -> None
def _run_template_refresh(args: argparse.Namespace) -> TemplateStep
def _select_agent_interactive(detected: list[tuple[str, str]]) -> str | None
def _run_agent_selection(args: argparse.Namespace) -> str | None
```

## Classes

```
class TemplateStep(Enum):
    REFRESHED = 'refreshed'
    CURRENT = 'current'
    DECLINED = 'declined'
    SKIPPED = 'skipped'

    @property
    def records_completion(self) -> bool
```
