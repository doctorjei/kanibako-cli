"""Extract embedded modules from Bun SEA (Single Executable Application) binaries.

Bun SEA format (ELF/Mach-O/PE):

    [native binary][bun data...][OFFSETS (32 bytes)]["\\n---- Bun! ----\\n"][u64 totalByteCount]

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
_MODULE_STRUCT_SIZE = 52


class BunSEAError(Exception):
    """Error parsing a Bun SEA binary."""


@dataclass
class BunModule:
    """A module embedded in a Bun SEA binary."""

    name: str
    content_offset: int  # absolute file offset
    content_length: int


def _parse_header(f) -> tuple[int, int, int]:
    """Parse the Bun SEA trailer and return (data_start, modules_offset, modules_length).

    All returned offsets are absolute file positions.
    """
    f.seek(0, 2)
    size = f.tell()
    trailer_size = 8 + len(_BUN_MARKER) + _OFFSETS_SIZE
    if size < trailer_size:
        raise BunSEAError("File too small to be a Bun SEA binary")

    # Read marker
    f.seek(-(8 + len(_BUN_MARKER)), 2)
    marker = f.read(len(_BUN_MARKER))
    if marker != _BUN_MARKER:
        raise BunSEAError("Bun SEA marker not found")

    # Read OFFSETS
    f.seek(-(8 + len(_BUN_MARKER) + _OFFSETS_SIZE), 2)
    offsets = f.read(_OFFSETS_SIZE)
    byte_count = struct.unpack("<Q", offsets[0:8])[0]
    mod_off = struct.unpack("<I", offsets[8:12])[0]
    mod_len = struct.unpack("<I", offsets[12:16])[0]

    marker_abs = size - 8 - len(_BUN_MARKER)
    data_start = marker_abs - _OFFSETS_SIZE - byte_count
    if data_start < 0:
        raise BunSEAError(
            f"Invalid data_start ({data_start}): byteCount={byte_count} exceeds file size"
        )

    return data_start, data_start + mod_off, mod_len


def list_modules(binary_path: Path) -> list[BunModule]:
    """List all modules embedded in a Bun SEA binary."""
    with open(binary_path, "rb") as f:
        data_start, modules_abs, modules_len = _parse_header(f)

        if modules_len % _MODULE_STRUCT_SIZE != 0:
            raise BunSEAError(
                f"Module table size {modules_len} not divisible by {_MODULE_STRUCT_SIZE}"
            )
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

            modules.append(BunModule(
                name=name,
                content_offset=data_start + c_off,
                content_length=c_len,
            ))

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
    raise BunSEAError(
        f"Module ending with '{name_suffix}' not found. "
        f"Available: {available}"
    )


def extract_cli_js(binary_path: Path) -> bytes:
    """Extract the cli.js bundle from a Bun SEA binary."""
    return extract_module(binary_path, "cli.js")


def cli_js_hash(binary_path: Path) -> str:
    """Return the SHA-256 hex digest of the cli.js content."""
    content = extract_cli_js(binary_path)
    return hashlib.sha256(content).hexdigest()
