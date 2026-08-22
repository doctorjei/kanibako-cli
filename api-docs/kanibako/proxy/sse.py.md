# `src/kanibako/proxy/sse.py` — API surface

_Signatures only: no comments, no docstrings, no bodies._
**GENERATED — do not hand-edit; regenerate with `notebook/scripts/dev-tools/gen-api-doc.py`.**
Prose for these symbols lives in `llm-docs/kanibako/proxy/sse.py.md`.


## Variables

```
logger = get_logger('proxy.sse')
_DELTA_SHAPES: Mapping[str, _DeltaShape] = {'text': _DeltaShape('text', '', 'text_delta', 'text', lambda value: value), 'thinking': _DeltaShape('thinking', '', 'thinking_delta', 'thinking', lambda value: value), 'tool_use': _DeltaShape('input', {}, 'input_json_delta', 'partial_json', _serialize)}
```

## Functions
```
def format_frame(event: Mapping[str, Any]) -> str
def synthesize_stream(response: Mapping[str, Any]) -> Iterator[str]
def iter_events(response: Mapping[str, Any]) -> Iterator[dict[str, Any]]
def _serialize(value: Any) -> str
def _message_start(response: Mapping[str, Any]) -> dict[str, Any]
def _message_delta(response: Mapping[str, Any]) -> dict[str, Any]
def _content_block_events(index: int, block: Any) -> Iterator[dict[str, Any]]
def _verbatim_block_events(index: int, block: Any, block_type: Any) -> Iterator[dict[str, Any]]
```

## Classes

```
class _DeltaShape(NamedTuple):
    source_field: str
    start_value: Any
    delta_type: str
    delta_field: str
    encode: Callable[[Any], Any]
```
