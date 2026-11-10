"""The credential wire validates completely without exposing material in diagnostics."""

from __future__ import annotations

import io
import json
import os
import signal
import struct
import subprocess
import sys
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import kanibako.secret_delivery as delivery
from kanibako.secret_delivery import (
  SecretDeliveryError,
  SecretPayload,
  SecretValue,
  decode_payload,
  encode_payload,
  iter_payload,
  read_payload,
)


SYNTHETIC_SECRET = b"synthetic-private-material-246"


def wire(records: list[tuple[bytes, bytes]], *, magic: bytes = b"KANISEC\x01") -> bytes:
  result = bytearray(magic + struct.pack("!I", len(records)))
  for name, value in records:
    result += struct.pack("!I", len(name)) + name + struct.pack("!Q", len(value)) + value
  return bytes(result)


class PartialReader(io.BytesIO):
  def read(self, size: int | None = -1) -> bytes:
    return super().read(min(size if size is not None else 3, 3))


class CheckedReader(io.BytesIO):
  def __init__(self, data: bytes) -> None:
    super().__init__(data)
    self.requests: list[int] = []

  def read(self, size: int | None = -1) -> bytes:
    assert size is not None and 0 < size <= 64 * 1024
    self.requests.append(size)
    return super().read(size)


def test_exact_versioned_wire_and_round_trip() -> None:
  payload = SecretPayload((SecretValue("TEST_TOKEN", SYNTHETIC_SECRET), SecretValue("_X2", b"")))
  expected = wire([(b"TEST_TOKEN", SYNTHETIC_SECRET), (b"_X2", b"")])
  assert encode_payload(payload) == expected
  assert b"".join(iter_payload(payload)) == expected
  assert decode_payload(expected) == payload
  assert payload.names == ("TEST_TOKEN", "_X2")


def test_empty_payload_round_trip() -> None:
  payload = SecretPayload()
  assert encode_payload(payload) == b"KANISEC\x01\0\0\0\0"
  assert decode_payload(encode_payload(payload)) == payload
  assert payload.names == ()


def test_large_values_long_names_and_shell_bytes_have_no_policy_cap() -> None:
  name = "TEST_" + "X" * 131072
  value = (b"'\"$`\\\n\r\x00\xff" + SYNTHETIC_SECRET) * 32768
  payload = SecretPayload((SecretValue(name, value),))
  reader = CheckedReader(encode_payload(payload))
  assert read_payload(reader) == payload
  assert reader.requests
  assert max(reader.requests) == 64 * 1024


def test_partial_reads_and_stream_ownership() -> None:
  payload = SecretPayload((SecretValue("TEST_TOKEN", SYNTHETIC_SECRET),))
  stream = PartialReader(encode_payload(payload))
  assert read_payload(stream) == payload
  assert not stream.closed
  assert stream.tell() == len(encode_payload(payload))


def test_carriers_copy_input_and_hide_values() -> None:
  values = [SecretValue("TEST_TOKEN", SYNTHETIC_SECRET)]
  payload = SecretPayload(values)  # type: ignore[arg-type]
  values.clear()
  assert payload.values == (SecretValue("TEST_TOKEN", SYNTHETIC_SECRET),)
  assert isinstance(payload.values, tuple)
  for shown in (repr(payload), str(payload), repr(payload.values), repr(payload.values[0])):
    assert SYNTHETIC_SECRET.decode() not in shown
  assert repr(payload.values[0]) == "SecretValue(name='TEST_TOKEN')"
  with pytest.raises(FrozenInstanceError):
    payload.values = ()  # type: ignore[misc]
  with pytest.raises(FrozenInstanceError):
    payload.values[0].value = b"changed"  # type: ignore[misc]


@pytest.mark.parametrize("name", ["", "9BAD", "BAD-NAME", "BAD.NAME", "A\n", "A=1", "é"])
def test_carrier_rejects_invalid_names_without_echo(name: str) -> None:
  with pytest.raises(SecretDeliveryError, match="invalid credential variable name") as caught:
    SecretValue(name, SYNTHETIC_SECRET)
  assert SYNTHETIC_SECRET.decode() not in repr(caught.value)
  assert caught.value.__context__ is None


@pytest.mark.parametrize(
  "value", ["synthetic-private-material-246", bytearray(SYNTHETIC_SECRET), None]
)
def test_carrier_rejects_nonbytes_without_echo(value: object) -> None:
  with pytest.raises(SecretDeliveryError, match="must be bytes") as caught:
    SecretValue("TEST_TOKEN", value)  # type: ignore[arg-type]
  assert SYNTHETIC_SECRET.decode() not in repr(caught.value)


def test_carrier_rejects_duplicate_and_invalid_entries() -> None:
  entry = SecretValue("TEST_TOKEN", SYNTHETIC_SECRET)
  with pytest.raises(SecretDeliveryError, match="duplicate"):
    SecretPayload((entry, entry))
  with pytest.raises(SecretDeliveryError, match="invalid credential payload entry") as caught:
    SecretPayload((SYNTHETIC_SECRET,))  # type: ignore[arg-type]
  assert SYNTHETIC_SECRET.decode() not in repr(caught.value)


@pytest.mark.parametrize(
  "frame",
  [
    wire([(b"TEST_TOKEN", SYNTHETIC_SECRET)], magic=b"KANISEC\x02"),
    wire([(b"TEST_TOKEN", SYNTHETIC_SECRET)]) + SYNTHETIC_SECRET,
    wire([(b"TEST_TOKEN", SYNTHETIC_SECRET), (b"TEST_TOKEN", b"second")]),
    wire([(b"", SYNTHETIC_SECRET)]),
    wire([(b"9BAD", SYNTHETIC_SECRET)]),
    wire([(b"BAD-NAME", SYNTHETIC_SECRET)]),
    wire([(b"BAD\n", SYNTHETIC_SECRET)]),
    wire([(b"\xff" + SYNTHETIC_SECRET, b"value")]),
  ],
  ids=[
    "version",
    "trailing",
    "duplicate",
    "empty-name",
    "leading-digit",
    "punctuation",
    "newline",
    "nonascii",
  ],
)
def test_decode_rejects_whole_frame_without_leaking(frame: bytes) -> None:
  with pytest.raises(SecretDeliveryError) as caught:
    decode_payload(frame)
  assert caught.value.__cause__ is None
  assert caught.value.__context__ is None
  assert SYNTHETIC_SECRET.decode() not in repr(caught.value)
  assert SYNTHETIC_SECRET.decode() not in "".join(traceback.format_exception(caught.value))


def test_every_truncated_prefix_is_refused() -> None:
  complete = wire([(b"TEST_TOKEN", SYNTHETIC_SECRET), (b"TEST_OTHER", b"another")])
  for length in range(len(complete)):
    with pytest.raises(SecretDeliveryError, match="incomplete"):
      decode_payload(complete[:length])
  assert decode_payload(complete).names == ("TEST_TOKEN", "TEST_OTHER")


@pytest.mark.parametrize(
  "prefix",
  [
    b"KANISEC\x01" + struct.pack("!I", 2**32 - 1),
    b"KANISEC\x01" + struct.pack("!II", 1, 2**32 - 1),
    b"KANISEC\x01" + struct.pack("!II", 1, 1) + b"X" + struct.pack("!Q", 2**64 - 1),
  ],
  ids=["record-count", "name-length", "value-length"],
)
def test_huge_advertised_lengths_read_in_chunks_and_fail_on_eof(prefix: bytes) -> None:
  reader = CheckedReader(prefix)
  with pytest.raises(SecretDeliveryError, match="incomplete"):
    read_payload(reader)
  assert max(reader.requests) <= 64 * 1024


@pytest.mark.parametrize("failure", [OSError, ValueError, TypeError, OverflowError, RuntimeError])
@pytest.mark.parametrize("at_eof", [False, True])
def test_reader_errors_cannot_leak_through_exception_chains(
  failure: type[Exception], at_eof: bool
) -> None:
  class BrokenReader(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
      if not at_eof or self.tell() == len(self.getvalue()):
        raise failure(SYNTHETIC_SECRET.decode())
      return super().read(size)

  reader = BrokenReader(encode_payload(SecretPayload()))
  with pytest.raises(SecretDeliveryError, match="cannot read") as caught:
    read_payload(reader)
  assert caught.value.__context__ is None
  assert caught.value.__cause__ is None
  assert SYNTHETIC_SECRET.decode() not in "".join(traceback.format_exception(caught.value))
  assert not reader.closed


@pytest.mark.parametrize("reply", [None, "synthetic-private-material-246", SYNTHETIC_SECRET * 3])
def test_invalid_reader_results_are_sanitized(reply: object) -> None:
  class InvalidReader(io.BytesIO):
    def read(self, size: int | None = -1) -> bytes:
      return reply  # type: ignore[return-value]

  with pytest.raises(SecretDeliveryError, match="cannot read") as caught:
    read_payload(InvalidReader())
  assert SYNTHETIC_SECRET.decode() not in repr(caught.value)


def run_receiver(
  frame: bytes, command: list[str]
) -> tuple[subprocess.CompletedProcess[bytes], bytes, int]:
  readfd, writefd = os.pipe()
  ackread, ackwrite = os.pipe()
  root = str(Path(delivery.__file__).resolve().parents[1])
  bootstrap = (
    "import os,sys;sys.path.insert(0,sys.argv[1]);"
    "r,a=map(int,sys.argv[2:4]);os.dup2(r,3);os.dup2(a,4);"
    "[os.close(fd) for fd in (r,a) if fd not in (3,4)];"
    "from kanibako.secret_delivery import main;raise SystemExit(main(sys.argv[4:]))"
  )
  process = subprocess.Popen(
    [sys.executable, "-I", "-c", bootstrap, root, str(readfd), str(ackwrite), *command],
    pass_fds=(readfd, ackwrite),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
  )
  os.close(readfd)
  os.close(ackwrite)
  try:
    os.write(writefd, frame)
    os.close(writefd)
    writefd = -1
    stdout, stderr = process.communicate(timeout=5)
    ack = os.read(ackread, 2)
    return (
      subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr),
      ack,
      process.pid,
    )
  finally:
    if writefd >= 0:
      os.close(writefd)
    os.close(ackread)
    if process.poll() is None:
      process.kill()
      process.wait(timeout=2)


def test_one_shot_receiver_exports_closes_and_execs_same_process() -> None:
  token = "synthetic-'\"$`\\-é"
  payload = SecretPayload((SecretValue("TEST_TOKEN", token.encode()),))
  command = [
    sys.executable,
    "-I",
    "-c",
    (
      "import os,json;closed=[]\n"
      "for fd in (3,4):\n"
      " try:os.fstat(fd)\n"
      " except OSError:closed.append(fd)\n"
      "print(json.dumps([os.getpid(),os.environ['TEST_TOKEN'],closed]));raise SystemExit(47)"
    ),
  ]
  result, ack, pid = run_receiver(encode_payload(payload), command)
  assert result.returncode == 47
  assert not result.stderr
  assert ack == delivery.SECRET_READY_ACK
  assert json.loads(result.stdout) == [pid, token, [3, 4]]


@pytest.mark.parametrize(
  "frame", [b"broken", wire([(b"TEST_TOKEN", b"bad\x00value")]), wire([(b"TEST_TOKEN", b"\xff")])]
)
def test_receiver_failure_has_no_ack_or_command_and_no_material(frame: bytes) -> None:
  result, ack, _ = run_receiver(frame, [sys.executable, "-I", "-c", "print('FORBIDDEN')"])
  assert result.returncode == 125
  assert ack == b""
  assert result.stdout == b""
  assert result.stderr == b"required PG credential delivery failed; restart the box\n"


def test_receiver_requires_command_before_consuming_frame() -> None:
  result, ack, _ = run_receiver(encode_payload(SecretPayload()), [])
  assert result.returncode == 125
  assert ack == b""
  assert result.stdout == b""


def test_one_shot_exec_restores_native_sigpipe_default() -> None:
  payload = SecretPayload((SecretValue("TEST_TOKEN", SYNTHETIC_SECRET),))
  result, ack, _ = run_receiver(
    encode_payload(payload), ["/bin/sh", "-c", "kill -PIPE $$; printf FORBIDDEN"]
  )
  assert result.returncode == -signal.SIGPIPE
  assert result.stdout == b""
  assert ack == delivery.SECRET_READY_ACK


def test_failed_environment_validation_closes_pipes_without_exception_material() -> None:
  r, w = os.pipe()
  ar, aw = os.pipe()
  os.write(w, wire([(b"TEST_TOKEN", b"\xff" + SYNTHETIC_SECRET)]))
  os.close(w)
  try:
    with pytest.raises(SecretDeliveryError) as caught:
      delivery.receive_environment(data_fd=r, ack_fd=aw)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert SYNTHETIC_SECRET.decode() not in repr(caught.value)
    for fd in (r, aw):
      with pytest.raises(OSError):
        os.fstat(fd)
    assert os.read(ar, 2) == b""
  finally:
    os.close(ar)
