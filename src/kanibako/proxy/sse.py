"""Synthesize an Anthropic SSE event stream from a complete ``/v1/messages`` response.

The DE-STREAM half of the proxy, and deliberately PURE: a complete
(non-streaming) response object in, SSE frames out.  No sockets, no upstream, no
configuration.  Everything that decides *whether* to de-stream lives in
``server``; this module only knows *how*.

THE ORDER, which is the contract (design §"The DE-STREAM rule, precisely"):

    message_start           the response envelope, content emptied
    content_block_start     ┐
    content_block_delta     ├ once per content block, in index order
    content_block_stop      ┘
    message_delta           the real stop_reason and usage
    message_stop

Which delta each block type contributes is stated once, in ``_DELTA_SHAPES``.

⚑ THE INCREMENTAL FIELD IS EMPTIED IN ``content_block_start`` and delivered by
the delta, never both.  A client accumulates start-value + deltas, so shipping
the value in both places yields it TWICE.  That is why ``_DeltaShape`` carries a
``start_value``: the rule is in the data, not in a caller's discipline.

⚑ ONE GATEWAY QUIRK, MEASURED TWICE AND DELIBERATELY NOT IMITATED: the LiteLLM
gateway emits ``message_start`` TWICE at the head of a real stream.  A stream
synthesized here has exactly one.  Anyone diffing the two will find that
duplicate; it is the gateway's, not a defect on this side.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from typing import Any, NamedTuple

from kanibako.log import get_logger

logger = get_logger("proxy.sse")


class _DeltaShape(NamedTuple):
  """How one content-block type splits into a ``content_block_start`` plus its delta."""

  source_field: str            # block field holding the value that arrives incrementally
  start_value: Any             # what that field holds in content_block_start (empty accumulator)
  delta_type: str              # ``type`` of the content_block_delta payload
  delta_field: str             # the delta payload's field carrying the value
  encode: Callable[[Any], Any]  # block value -> delta value


def _serialize(value: Any) -> str:
  """Serialize a tool-call ``input`` object for ``input_json_delta.partial_json``."""
  return json.dumps(value)


#: The block types this module knows how to stream.  Anything else falls back to
#: ``_verbatim_block_events`` — see that function for why it is a fallback and
#: not a refusal.
_DELTA_SHAPES: Mapping[str, _DeltaShape] = {
  "text": _DeltaShape("text", "", "text_delta", "text", lambda value: value),
  "thinking": _DeltaShape("thinking", "", "thinking_delta", "thinking", lambda value: value),
  "tool_use": _DeltaShape("input", {}, "input_json_delta", "partial_json", _serialize),
}


def format_frame(event: Mapping[str, Any]) -> str:
  """Encode one event object as an SSE frame.

  The ``event:`` name is the payload's OWN ``type`` — Anthropic's two spellings
  of the event name always agree, so there is one source for it here too.
  """
  body = json.dumps(event, separators=(",", ":"))
  return f"event: {event['type']}\ndata: {body}\n\n"


def synthesize_stream(response: Mapping[str, Any]) -> Iterator[str]:
  """Yield the SSE frames a streaming client expects for a complete *response*."""
  for event in iter_events(response):
    yield format_frame(event)


def iter_events(response: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
  """Yield the event objects for a complete *response*, in stream order.

  Separate from :func:`synthesize_stream` so the SHAPE and the WIRE ENCODING can
  each be pinned on their own; a test that asserts on parsed events does not
  re-implement the framing to get at them.
  """
  yield _message_start(response)
  content = response.get("content") or []
  for index, block in enumerate(content):
    yield from _content_block_events(index, block)
  yield _message_delta(response)
  yield {"type": "message_stop"}


def _message_start(response: Mapping[str, Any]) -> dict[str, Any]:
  """The opening envelope: the response itself, with everything not-yet-known blanked.

  ⚑ ``output_tokens`` IS ZEROED HERE, and that is measured, not preferred.  A
  live ``stream:true`` call on this gateway opens with::

      message_start.usage       {"input_tokens": 0, "output_tokens": 0,
                                 "cache_creation_input_tokens": 0,
                                 "cache_read_input_tokens": 0}
      message_start.content     []
      message_start.stop_reason null
      message_delta             {"delta": {"stop_reason": "tool_use", ...},
                                 "usage": {"input_tokens": 21146, "output_tokens": 25}}

  Upstream Anthropic differs in one direction only — it opens with a real
  ``input_tokens`` and a tiny ``output_tokens`` — so ``input_tokens`` and the
  cache fields are carried through as the response reported them.  In neither
  shape does the FINAL output count appear at the head of the stream; it belongs
  to ``message_delta`` alone.

  ``stop_sequence`` goes with ``stop_reason``: a stop sequence without a stop
  reason is not a state that exists.
  """
  envelope = dict(response)
  envelope["content"] = []
  envelope["stop_reason"] = None
  envelope["stop_sequence"] = None
  usage = response.get("usage")
  if isinstance(usage, Mapping):
    envelope["usage"] = {**usage, "output_tokens": 0}
  return {"type": "message_start", "message": envelope}


def _message_delta(response: Mapping[str, Any]) -> dict[str, Any]:
  """The closing delta: the REAL stop reason and the token accounting."""
  return {
    "type": "message_delta",
    "delta": {
      "stop_reason": response.get("stop_reason"),
      "stop_sequence": response.get("stop_sequence"),
    },
    "usage": response.get("usage", {}),
  }


def _content_block_events(index: int, block: Any) -> Iterator[dict[str, Any]]:
  """Yield start → delta → stop for one content block at *index*."""
  block_type = block.get("type") if isinstance(block, Mapping) else None
  shape = _DELTA_SHAPES.get(block_type) if isinstance(block_type, str) else None
  if shape is None:
    yield from _verbatim_block_events(index, block, block_type)
    return

  start_block = dict(block)
  start_block[shape.source_field] = shape.start_value
  yield {"type": "content_block_start", "index": index, "content_block": start_block}
  yield {
    "type": "content_block_delta",
    "index": index,
    "delta": {
      "type": shape.delta_type,
      shape.delta_field: shape.encode(block.get(shape.source_field, shape.start_value)),
    },
  }
  yield {"type": "content_block_stop", "index": index}


def _verbatim_block_events(index: int, block: Any, block_type: Any) -> Iterator[dict[str, Any]]:
  """Deliver an unrecognised block whole in ``content_block_start``, with no delta.

  ⚑ A FALLBACK, not a silent accept, and not a refusal — the choice is
  deliberate and this is the note that says so.  Upstream response data is not
  our closed keyspace: the block types are Anthropic's and the set grows
  (``redacted_thinking``, server-side tools).  Refusing an unknown one would
  brick a session over a field we do not even need to interpret, while
  inventing a delta for it would fabricate a shape.  Shipping the block entire
  in its ``content_block_start`` is lossless and forward-compatible; the debug
  line names the TYPE only, never the content.
  """
  logger.debug("proxy: content block type %r streamed whole (no delta shape)", block_type)
  yield {"type": "content_block_start", "index": index, "content_block": block}
  yield {"type": "content_block_stop", "index": index}
