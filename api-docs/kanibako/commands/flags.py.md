# `src/kanibako/commands/flags.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/commands/flags.py.md`.


## Variables

```
AGENT_FLAG_COMMANDS: frozenset[str] = frozenset({'start', 'box start', 'create', 'box create', 'reauth', 'agent reauth'})
BOX_FLAG_COMMANDS: frozenset[str] = frozenset({'start', 'shell', 'box start', 'box shell', 'stop', 'box stop', 'box convert', 'box move', 'box duplicate', 'box info', 'box set', 'box reset', 'box get', 'box show', 'box diagnose', 'box rm', 'box register', 'box remap', 'reauth', 'agent reauth', 'workset disconnect'})
NULL_FLAG_HELP_TEMPLATE = "SUPPRESS the value this key would otherwise inherit: writes an explicit null (present-None) at this scope, so the scopes above it stop supplying the key and the consumer sees it as dropped (spec section 2h). This WRITES an override rather than removing one - to undo it and get the inherited value back, use the sibling 'reset' verb ('{undo}')."
_AGENT_FLAG_EXCLUDE: frozenset[str] = frozenset({'setup'})
_BAIL = -1
```

## Functions
```
def add_null_flag(parser: argparse.ArgumentParser, *, undo: str) -> None
def hoist_optionals(parser: argparse.ArgumentParser, argv: list[str]) -> list[str]
def inject_blanket_flags(parser: argparse.ArgumentParser) -> None
def command_key(args: argparse.Namespace) -> str
def resolve_subject_value(positional: str | None, box_flag: str | None) -> str | None
def check_flag_relevance(args: argparse.Namespace) -> None
def _add_agent_flag(parser: argparse.ArgumentParser) -> None
def _add_box_flag(parser: argparse.ArgumentParser) -> None
def _take_for(nargs: object) -> int
def _option_take(option_nargs: dict[str, object], parser: argparse.ArgumentParser, token: str) -> int | None
def _splits_positionals(positionals: 'list[argparse.Action]') -> bool
def _has_option(parser: argparse.ArgumentParser, option: str) -> bool
def _walk(parser: argparse.ArgumentParser, prefix: tuple[str, ...]) -> None
def _find_subparsers_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None
```

## Classes

```
class OptionsAnywhereParser(argparse.ArgumentParser):
    def parse_known_args(self, args: 'list[str] | None'=None, namespace: argparse.Namespace | None=None) -> 'tuple[argparse.Namespace, list[str]]'

class FlagRelevanceError(Exception):
```
