"""Extract embedded modules from Bun SEA (Single Executable Application) binaries.

The payload is ``[bun data...][OFFSETS (32 bytes)]["\\n---- Bun! ----\\n"]``.
Bun stores it in one of two layouts:

    Appended (older builds):
        [native binary][payload][u64 totalByteCount]          -- marker 8 bytes before EOF
    ELF ``.bun`` section (current Linux builds):
        [native binary][.bun: u64 payloadLength][payload][other sections][section headers]

In the section layout the marker sits inside the file, and nothing follows it
inside the section.

OFFSETS struct (32 bytes):
    u64 byteCount          — size of the data blob
    u32 modulesPtr.offset  — module table offset from data start
    u32 modulesPtr.length  — module table size in bytes
    u32 entryPointId       — index of the entry module
    u32 compileExecArgvPtr.offset
    u32 compileExecArgvPtr.length
    u32 flags

Module struct (52 bytes, Bun >= 1.3.7):
    StringPointer name      (offset u32, length u32)
    StringPointer contents  (offset u32, length u32)
    StringPointer sourcemap (offset u32, length u32)
    StringPointer bytecode  (offset u32, length u32)
    StringPointer moduleInfo (offset u32, length u32)
    StringPointer bytecodeOriginPath (offset u32, length u32)
    4 bytes enum/flags

All offsets are relative to data_start.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from pathlib import Path

_BUN_MARKER = b"\n---- Bun! ----\n"
_OFFSETS_SIZE = 32
_ELF_SECTION = b".bun"
_MODULE_STRUCT_SIZE = 52


class BunSEAError(Exception):
  """Error parsing a Bun SEA binary."""


@dataclass
class BunModule:
  """A module embedded in a Bun SEA binary."""

  name: str
  content_offset: int  # absolute file offset
  content_length: int


def _section_trailer_end(f, size: int) -> int | None:
  """Return the file offset just past the payload in an ELF ``.bun`` section.

  Returns None if the file is not a 64-bit little-endian ELF with a ``.bun``
  section.
  """
  f.seek(0)
  ident = f.read(64)
  if len(ident) < 64 or ident[:4] != b"\x7fELF" or ident[4] != 2 or ident[5] != 1:
    return None
  shoff = struct.unpack("<Q", ident[0x28:0x30])[0]
  shentsize, shnum, shstrndx = struct.unpack("<HHH", ident[0x3A:0x40])
  if shentsize < 64 or shnum == 0 or shstrndx >= shnum or shoff + shnum * shentsize > size:
    return None
  f.seek(shoff)
  table = f.read(shnum * shentsize)

  def section(i: int) -> tuple[int, int, int]:
    base = i * shentsize
    name = struct.unpack("<I", table[base : base + 4])[0]
    offset, length = struct.unpack("<QQ", table[base + 24 : base + 40])
    return name, offset, length

  _, strtab_off, strtab_len = section(shstrndx)
  if strtab_off + strtab_len > size:
    return None
  f.seek(strtab_off)
  strtab = f.read(strtab_len)
  for i in range(shnum):
    name, offset, length = section(i)
    if strtab[name : name + len(_ELF_SECTION) + 1] != _ELF_SECTION + b"\0":
      continue
    if length < 8 or offset + length > size:
      raise BunSEAError(f"ELF {_ELF_SECTION.decode()} section lies outside the file")
    f.seek(offset)
    payload_len = struct.unpack("<Q", f.read(8))[0]
    if payload_len > length - 8:
      raise BunSEAError(
        f"ELF {_ELF_SECTION.decode()} payload length {payload_len} exceeds "
        f"its section ({length - 8} bytes)"
      )
    return offset + 8 + payload_len
  return None


def _parse_header(f) -> tuple[int, int, int, int]:
  """Parse the Bun SEA trailer.

  Returns (data_start, modules_offset, modules_length, payload_end), where
  payload_end is the offset just past the marker.

  All returned offsets are absolute file positions.
  """
  f.seek(0, 2)
  size = f.tell()
  trailer_size = 8 + len(_BUN_MARKER) + _OFFSETS_SIZE
  if size < trailer_size:
    raise BunSEAError("File too small to be a Bun SEA binary")

  # Appended layout: the marker, then a u64, at the end of the file.
  f.seek(-(8 + len(_BUN_MARKER)), 2)
  if f.read(len(_BUN_MARKER)) == _BUN_MARKER:
    payload_end = size - 8
  else:
    section_end = _section_trailer_end(f, size)
    if section_end is None:
      raise BunSEAError("Bun SEA marker not found")
    payload_end = section_end
  marker_abs = payload_end - len(_BUN_MARKER)
  if marker_abs < _OFFSETS_SIZE:
    raise BunSEAError("Bun SEA marker not found")
  f.seek(marker_abs)
  if f.read(len(_BUN_MARKER)) != _BUN_MARKER:
    raise BunSEAError("Bun SEA marker not found")

  # Read OFFSETS
  f.seek(marker_abs - _OFFSETS_SIZE)
  offsets = f.read(_OFFSETS_SIZE)
  byte_count = struct.unpack("<Q", offsets[0:8])[0]
  mod_off = struct.unpack("<I", offsets[8:12])[0]
  mod_len = struct.unpack("<I", offsets[12:16])[0]

  data_start = marker_abs - _OFFSETS_SIZE - byte_count
  if data_start < 0:
    raise BunSEAError(
      f"Invalid data_start ({data_start}): byteCount={byte_count} exceeds file size"
    )

  return data_start, data_start + mod_off, mod_len, payload_end


def list_modules(binary_path: Path) -> list[BunModule]:
  """List all modules embedded in a Bun SEA binary."""
  with open(binary_path, "rb") as f:
    data_start, modules_abs, modules_len, _ = _parse_header(f)

    if modules_len % _MODULE_STRUCT_SIZE != 0:
      raise BunSEAError(f"Module table size {modules_len} not divisible by {_MODULE_STRUCT_SIZE}")
    n_modules = modules_len // _MODULE_STRUCT_SIZE

    f.seek(modules_abs)
    table = f.read(modules_len)

    modules: list[BunModule] = []
    for i in range(n_modules):
      base = i * _MODULE_STRUCT_SIZE
      name_off = struct.unpack("<I", table[base : base + 4])[0]
      name_len = struct.unpack("<I", table[base + 4 : base + 8])[0]
      c_off = struct.unpack("<I", table[base + 8 : base + 12])[0]
      c_len = struct.unpack("<I", table[base + 12 : base + 16])[0]

      f.seek(data_start + name_off)
      name = f.read(name_len).decode("utf-8", errors="replace")

      modules.append(
        BunModule(
          name=name,
          content_offset=data_start + c_off,
          content_length=c_len,
        )
      )

    return modules


def extract_module(binary_path: Path, name_suffix: str = "cli.js") -> bytes:
  """Extract a module's content by name suffix (default: cli.js)."""
  modules = list_modules(binary_path)
  for mod in modules:
    if mod.name.endswith(name_suffix):
      with open(binary_path, "rb") as f:
        f.seek(mod.content_offset)
        return f.read(mod.content_length)
  available = [m.name for m in modules]
  raise BunSEAError(f"Module ending with '{name_suffix}' not found. Available: {available}")


def extract_cli_js(binary_path: Path) -> bytes:
  """Extract the cli.js bundle from a Bun SEA binary."""
  return extract_module(binary_path, "cli.js")


def cli_js_hash(binary_path: Path) -> str:
  """Return a SHA-256 hex digest identifying the binary's bundled code.

  It hashes the ``cli.js`` module when there is one. Builds split into chunks
  have no ``cli.js`` (their entry is ``/$bunfs/root/cli``), so for them it
  hashes the whole payload, which covers every module.
  """
  modules = list_modules(binary_path)
  cli_js = next((m for m in modules if m.name.endswith("cli.js")), None)
  digest = hashlib.sha256()
  with open(binary_path, "rb") as f:
    if cli_js is not None:
      f.seek(cli_js.content_offset)
      digest.update(f.read(cli_js.content_length))
    else:
      data_start, _, _, payload_end = _parse_header(f)
      f.seek(data_start)
      remaining = payload_end - data_start
      while remaining > 0:
        chunk = f.read(min(remaining, 1 << 20))
        if not chunk:
          raise BunSEAError("Bun SEA payload is truncated")
        digest.update(chunk)
        remaining -= len(chunk)
  return digest.hexdigest()
