"""One-shot PG credential framing and in-box environment delivery."""

from __future__ import annotations

import io
import os
import re
import signal
import struct
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import BinaryIO, Final

from kanibako.agent_ref import ENV_VAR_NAME

_FRAME_MAGIC: Final[bytes] = b"KANISEC\x01"
_COUNT = struct.Struct("!I")
_LENGTH = struct.Struct("!Q")
_READ_CHUNK: Final[int] = 64 * 1024
SECRET_DATA_FD: Final[int] = 3
SECRET_ACK_FD: Final[int] = 4
SECRET_READY_ACK: Final[bytes] = b"R"


class SecretDeliveryError(ValueError):
  """Invalid credential transport; diagnostics never contain frame material."""


@dataclass(frozen=True)
class SecretValue:
  """One validated variable name and an opaque, repr-hidden value."""

  name: str
  value: bytes = field(repr=False)

  def __post_init__(self) -> None:
    if not isinstance(self.name, str) or re.fullmatch(ENV_VAR_NAME, self.name) is None:
      raise SecretDeliveryError("invalid credential variable name")
    if not isinstance(self.value, bytes):
      raise SecretDeliveryError("credential values must be bytes")


@dataclass(frozen=True)
class SecretPayload:
  """An immutable, duplicate-free snapshot of already-authorized values."""

  values: tuple[SecretValue, ...] = field(default=(), repr=False)

  def __post_init__(self) -> None:
    entries = tuple(self.values)
    names: set[str] = set()
    for entry in entries:
      if not isinstance(entry, SecretValue):
        raise SecretDeliveryError("invalid credential payload entry")
      if entry.name in names:
        raise SecretDeliveryError("duplicate credential variable name")
      names.add(entry.name)
    object.__setattr__(self, "values", entries)

  @property
  def names(self) -> tuple[str, ...]:
    """Plaintext-free names in wire order, without exposing the internal carrier."""
    return tuple(entry.name for entry in self.values)


def iter_payload(payload: SecretPayload) -> Iterator[bytes]:
  """Yield one versioned frame; callers own pumping deadlines and descriptor lifetime."""
  yield _FRAME_MAGIC
  yield _COUNT.pack(len(payload.values))
  for entry in payload.values:
    name = entry.name.encode("ascii")
    yield _COUNT.pack(len(name))
    yield name
    yield _LENGTH.pack(len(entry.value))
    yield entry.value


def encode_payload(payload: SecretPayload) -> bytes:
  """Encode a complete frame without fetching credentials."""
  return b"".join(iter_payload(payload))


def _read_piece(stream: BinaryIO, size: int) -> bytes:
  piece = None
  try:
    piece = stream.read(size)
  except Exception:
    pass
  if not isinstance(piece, bytes) or len(piece) > size:
    raise SecretDeliveryError("cannot read credential frame")
  return piece


def _read_exact(stream: BinaryIO, size: int) -> bytes:
  value = bytearray()
  while len(value) < size:
    piece = _read_piece(stream, min(size - len(value), _READ_CHUNK))
    if not piece:
      raise SecretDeliveryError("incomplete credential frame")
    value.extend(piece)
  return bytes(value)


def read_payload(stream: BinaryIO) -> SecretPayload:
  """Validate the entire frame and EOF before returning any values; leave the stream open."""
  if _read_exact(stream, len(_FRAME_MAGIC)) != _FRAME_MAGIC:
    raise SecretDeliveryError("unsupported credential frame version")
  count = _COUNT.unpack(_read_exact(stream, _COUNT.size))[0]
  values: list[SecretValue] = []
  names: set[str] = set()
  for _ in range(count):
    size = _COUNT.unpack(_read_exact(stream, _COUNT.size))[0]
    encoded_name = _read_exact(stream, size)
    if not encoded_name.isascii():
      raise SecretDeliveryError("invalid credential variable name")
    name = encoded_name.decode("ascii")
    if re.fullmatch(ENV_VAR_NAME, name) is None:
      raise SecretDeliveryError("invalid credential variable name")
    if name in names:
      raise SecretDeliveryError("duplicate credential variable name")
    names.add(name)
    length = _LENGTH.unpack(_read_exact(stream, _LENGTH.size))[0]
    values.append(SecretValue(name, _read_exact(stream, length)))
  if _read_piece(stream, 1):
    raise SecretDeliveryError("trailing credential frame data")
  return SecretPayload(tuple(values))


def decode_payload(frame: bytes) -> SecretPayload:
  """Decode an in-memory frame with the same complete-stream validation."""
  return read_payload(io.BytesIO(frame))


def receive_environment(
  *, data_fd: int = SECRET_DATA_FD, ack_fd: int = SECRET_ACK_FD
) -> Mapping[str, str]:
  """Consume and close both inherited pipes; acknowledge only validated environment values."""
  try:
    with os.fdopen(data_fd, "rb") as stream:
      payload = read_payload(stream)
    values: dict[str, str] = {}
    for entry in payload.values:
      value = entry.value.decode("utf-8")
      if "\0" in value:
        raise SecretDeliveryError("invalid credential environment value")
      values[entry.name] = value
    if os.write(ack_fd, SECRET_READY_ACK) != len(SECRET_READY_ACK):
      raise SecretDeliveryError("cannot acknowledge credential delivery")
    return values
  except Exception:
    pass
  finally:
    for fd in (data_fd, ack_fd):
      try:
        os.close(fd)
      except OSError:
        pass
  raise SecretDeliveryError("required PG credential delivery failed")


def main(argv: list[str] | None = None) -> int:
  """Read once, close the transport, then replace this process with the existing entrypoint."""
  args = list(sys.argv[1:] if argv is None else argv)
  if args[:1] == ["--"]:
    args.pop(0)
  try:
    if not args:
      raise SecretDeliveryError("PG credential delivery requires an entrypoint")
    values = receive_environment()
    os.environ.update(values)
    # Python ignores these at startup; the existing entrypoint needs native exec defaults.
    for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
      if hasattr(signal, name):
        signal.signal(getattr(signal, name), signal.SIG_DFL)
    os.execvp(args[0], args)
  except Exception:
    print("required PG credential delivery failed; restart the box", file=sys.stderr)
    return 125
  return 125


if __name__ == "__main__":
  raise SystemExit(main())
