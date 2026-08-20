"""Tests for kanibako.proxy.sse: SSE synthesis from a complete /v1/messages response."""

from __future__ import annotations

import json

from kanibako.proxy.sse import format_frame, iter_events, synthesize_stream

# The ENVELOPE of the recorded 2026-08-20 NaviGator fault, response fields only.
# The captured stream carried this message_start and then NO content block at
# all -- `stop_reason: tool_use` with nothing to use. It is the negative fixture:
# what the proxy exists to supply is exactly what is missing from it.
_CAPTURED_ENVELOPE = {
  "id": "msg_d87d37c8-644f-4a3d-886c-48e1f31a9f92",
  "type": "message",
  "role": "assistant",
  "model": "google/gemma-4-31b-it",
  "stop_reason": "tool_use",
  "stop_sequence": None,
  "usage": {"input_tokens": 21146, "output_tokens": 29},
}

_TOOL_BLOCK = {
  "type": "tool_use",
  "id": "toolu_01A",
  "name": "Bash",
  "input": {"command": "ls -la /tmp", "description": "List /tmp"},
}
_TEXT_BLOCK = {"type": "text", "text": "Checking the directory now."}
_THINKING_BLOCK = {"type": "thinking", "thinking": "The user wants a listing.", "signature": "sig"}


def _response(*blocks, **overrides):
  """A complete non-streaming response carrying *blocks*."""
  body = dict(_CAPTURED_ENVELOPE)
  body["content"] = list(blocks)
  body.update(overrides)
  return body


def _events(*blocks, **overrides):
  return list(iter_events(_response(*blocks, **overrides)))


def _of_type(events, wanted):
  return [event for event in events if event["type"] == wanted]


def _reconstruct(events):
  """Rebuild the content blocks the way a streaming client does: start value + deltas.

  This is the round-trip oracle. It models the CLIENT's accumulation rule, so a
  synthesis that shipped a value twice (or not at all) fails here rather than
  merely looking plausible frame by frame.
  """
  blocks: dict[int, dict] = {}
  partial_json: dict[int, str] = {}
  for event in events:
    if event["type"] == "content_block_start":
      blocks[event["index"]] = dict(event["content_block"])
    elif event["type"] == "content_block_delta":
      index, delta = event["index"], event["delta"]
      if delta["type"] == "text_delta":
        blocks[index]["text"] += delta["text"]
      elif delta["type"] == "thinking_delta":
        blocks[index]["thinking"] += delta["thinking"]
      elif delta["type"] == "input_json_delta":
        partial_json[index] = partial_json.get(index, "") + delta["partial_json"]
  for index, raw in partial_json.items():
    blocks[index]["input"] = json.loads(raw)
  return [blocks[index] for index in sorted(blocks)]


class TestFraming:
  def test_frame_is_event_name_then_data_then_blank_line(self):
    frame = format_frame({"type": "message_stop"})
    assert frame == 'event: message_stop\ndata: {"type":"message_stop"}\n\n'

  def test_event_name_always_equals_the_payload_type(self):
    # The rule, not an inventory: whatever events a response yields, the two
    # spellings of the name agree for every one of them.
    for event in _events(_TEXT_BLOCK, _TOOL_BLOCK, _THINKING_BLOCK):
      name = format_frame(event).split("\n", 1)[0]
      assert name == f"event: {event['type']}"

  def test_stream_frames_parse_back_to_the_events(self):
    response = _response(_TEXT_BLOCK, _TOOL_BLOCK)
    parsed = []
    for frame in synthesize_stream(response):
      assert frame.endswith("\n\n")
      head, data = frame.rstrip("\n").split("\n")
      parsed.append(json.loads(data.removeprefix("data: ")))
      assert head.removeprefix("event: ") == parsed[-1]["type"]
    assert parsed == list(iter_events(response))


class TestOrder:
  def test_single_block_order(self):
    assert [event["type"] for event in _events(_TEXT_BLOCK)] == [
      "message_start",
      "content_block_start",
      "content_block_delta",
      "content_block_stop",
      "message_delta",
      "message_stop",
    ]

  def test_multi_block_order_and_indices(self):
    events = _events(_TEXT_BLOCK, _TOOL_BLOCK, _THINKING_BLOCK)
    assert events[0]["type"] == "message_start"
    assert events[-2]["type"] == "message_delta"
    assert events[-1]["type"] == "message_stop"

    middle = events[1:-2]
    assert [event["type"] for event in middle] == [
      "content_block_start", "content_block_delta", "content_block_stop",
    ] * 3
    assert [event["index"] for event in middle] == [0, 0, 0, 1, 1, 1, 2, 2, 2]

  def test_message_stop_appears_once_and_last(self):
    events = _events(_TEXT_BLOCK, _TOOL_BLOCK)
    assert len(_of_type(events, "message_stop")) == 1
    assert events[-1] == {"type": "message_stop"}

  def test_empty_content_yields_only_the_three_envelope_events(self):
    # The shape of the recorded fault itself: a truthful envelope, no blocks.
    assert [event["type"] for event in _events()] == [
      "message_start", "message_delta", "message_stop",
    ]


class TestMessageStart:
  def test_envelope_is_the_response_with_content_emptied(self):
    message = _events(_TEXT_BLOCK)[0]["message"]
    assert message["content"] == []
    assert message["stop_reason"] is None
    assert message["stop_sequence"] is None

  def test_envelope_preserves_the_rest_of_the_response(self):
    message = _events(_TOOL_BLOCK)[0]["message"]
    assert message["id"] == _CAPTURED_ENVELOPE["id"]
    assert message["type"] == "message"
    assert message["role"] == "assistant"
    assert message["model"] == _CAPTURED_ENVELOPE["model"]

  def test_output_tokens_are_zero_at_the_head_of_the_stream(self):
    # Measured against a live stream:true call: the opening envelope carries no
    # output count. The final count belongs to message_delta alone.
    events = _events(_TOOL_BLOCK)
    assert events[0]["message"]["usage"] == {"input_tokens": 21146, "output_tokens": 0}
    assert events[-2]["usage"] == {"input_tokens": 21146, "output_tokens": 29}

  def test_cache_fields_are_carried_through_to_the_envelope(self):
    usage = {
      "input_tokens": 21146,
      "output_tokens": 29,
      "cache_creation_input_tokens": 512,
      "cache_read_input_tokens": 1024,
    }
    message = _events(_TOOL_BLOCK, usage=usage)[0]["message"]
    assert message["usage"] == {**usage, "output_tokens": 0}

  def test_no_usage_is_not_invented(self):
    response = {"id": "m", "type": "message", "role": "assistant", "content": []}
    assert "usage" not in list(iter_events(response))[0]["message"]

  def test_source_response_is_not_mutated(self):
    response = _response(_TEXT_BLOCK)
    list(iter_events(response))
    assert response["stop_reason"] == "tool_use"
    assert response["content"] == [_TEXT_BLOCK]
    assert response["usage"] == _CAPTURED_ENVELOPE["usage"]


class TestBlockTypes:
  def test_text_block_round_trips(self):
    events = _events(_TEXT_BLOCK)
    delta = _of_type(events, "content_block_delta")[0]["delta"]
    assert delta["type"] == "text_delta"
    assert _of_type(events, "content_block_start")[0]["content_block"]["text"] == ""
    assert _reconstruct(events) == [_TEXT_BLOCK]

  def test_thinking_block_round_trips_and_keeps_its_signature(self):
    events = _events(_THINKING_BLOCK)
    start = _of_type(events, "content_block_start")[0]["content_block"]
    assert start["thinking"] == ""
    assert start["signature"] == "sig"
    assert _of_type(events, "content_block_delta")[0]["delta"]["type"] == "thinking_delta"
    assert _reconstruct(events) == [_THINKING_BLOCK]

  def test_tool_use_block_round_trips(self):
    events = _events(_TOOL_BLOCK)
    start = _of_type(events, "content_block_start")[0]["content_block"]
    assert start["input"] == {}
    assert start["id"] == "toolu_01A"
    assert start["name"] == "Bash"
    assert _reconstruct(events) == [_TOOL_BLOCK]

  def test_tool_use_input_is_one_delta_of_valid_json(self):
    deltas = _of_type(_events(_TOOL_BLOCK), "content_block_delta")
    assert len(deltas) == 1
    assert deltas[0]["delta"]["type"] == "input_json_delta"
    assert json.loads(deltas[0]["delta"]["partial_json"]) == _TOOL_BLOCK["input"]

  def test_tool_use_input_survives_nesting_and_non_ascii(self):
    block = {
      "type": "tool_use",
      "id": "toolu_02B",
      "name": "Edit",
      "input": {"edits": [{"old": "α β", "new": 'quote " and \\ back'}], "count": 2, "dry": False},
    }
    assert _reconstruct(_events(block)) == [block]

  def test_multi_block_response_round_trips_whole(self):
    blocks = [_TEXT_BLOCK, _TOOL_BLOCK, _THINKING_BLOCK]
    assert _reconstruct(_events(*blocks)) == blocks

  def test_unknown_block_type_ships_whole_in_its_start_with_no_delta(self):
    block = {"type": "redacted_thinking", "data": "opaque-payload"}
    events = _events(block)
    assert [event["type"] for event in events[1:-2]] == [
      "content_block_start", "content_block_stop",
    ]
    assert events[1]["content_block"] == block


class TestMessageDelta:
  def test_carries_the_real_stop_reason_and_usage(self):
    delta = _events(_TOOL_BLOCK)[-2]
    assert delta["type"] == "message_delta"
    assert delta["delta"]["stop_reason"] == "tool_use"
    assert delta["delta"]["stop_sequence"] is None
    assert delta["usage"] == {"input_tokens": 21146, "output_tokens": 29}

  def test_carries_a_stop_sequence_when_the_response_had_one(self):
    delta = _events(_TEXT_BLOCK, stop_reason="stop_sequence", stop_sequence="END")[-2]
    assert delta["delta"] == {"stop_reason": "stop_sequence", "stop_sequence": "END"}

  def test_usage_defaults_to_empty_when_absent(self):
    response = {"id": "m", "type": "message", "role": "assistant", "content": []}
    assert list(iter_events(response))[-2]["usage"] == {}


class TestGatewayShape:
  def test_tool_use_start_matches_the_measured_gateway_frame(self):
    # Pinned against a live stream:true capture: id and name present, input {}.
    block = {
      "type": "tool_use",
      "id": "chatcmpl-tool-a1b2c3",
      "name": "Bash",
      "input": {"command": "ls", "description": "List"},
    }
    start = _of_type(_events(block), "content_block_start")[0]
    assert start == {
      "type": "content_block_start",
      "index": 0,
      "content_block": {
        "type": "tool_use",
        "id": "chatcmpl-tool-a1b2c3",
        "name": "Bash",
        "input": {},
      },
    }


class TestFaultSignature:
  def test_synthesis_supplies_the_block_the_recorded_fault_omitted(self):
    # The fault: stop_reason `tool_use` with zero content_block_start events.
    # Fed the SAME envelope plus the tool block the non-streaming call returns,
    # synthesis produces the content_block_start that was missing.
    events = _events(_TOOL_BLOCK)
    starts = _of_type(events, "content_block_start")
    assert len(starts) == 1
    assert starts[0]["content_block"]["name"] == "Bash"
    assert events[-2]["delta"]["stop_reason"] == "tool_use"
