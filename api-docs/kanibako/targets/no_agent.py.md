# `src/kanibako/targets/no_agent.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/targets/no_agent.py.md`.


## Classes

```
class NoAgentTarget(Target):
    @property
    def name(self) -> str
    @property
    def display_name(self) -> str
    @property
    def has_binary(self) -> bool
    def detect(self) -> AgentInstall | None
    def refresh_credentials(self, home: Path) -> None
    def writeback_credentials(self, home: Path) -> None
    def generate_agent_config(self) -> AgentConfig
```
