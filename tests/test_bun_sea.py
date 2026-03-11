"""Tests for Bun SEA binary parsing."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest

from kanibako.bun_sea import (
    BunSEAError,
    _BUN_MARKER,
    cli_js_hash,
    extract_cli_js,
    extract_module,
    list_modules,
)


def _build_fake_sea(tmp_path: Path, modules: list[tuple[str, bytes]]) -> Path:
    """Build a minimal fake Bun SEA binary for testing.

    *modules* is a list of (name, content) tuples.
    """
    path = tmp_path / "fake-bun-sea"

    # ELF stub (just some bytes so data_start > 0)
    elf_stub = b"\x7fELF" + b"\x00" * 60

    # Build module content and name blobs
    blob_parts: list[bytes] = []
    current_offset = 0

    # Reserve space for all content and names first
    name_entries: list[tuple[int, int]] = []  # (offset, length) pairs
    content_entries: list[tuple[int, int]] = []

    for name, content in modules:
        name_bytes = name.encode("utf-8")
        content_entries.append((current_offset, len(content)))
        blob_parts.append(content)
        current_offset += len(content)

        name_entries.append((current_offset, len(name_bytes)))
        blob_parts.append(name_bytes)
        current_offset += len(name_bytes)

    # Build module table
    module_table = b""
    for i, (_name, _content) in enumerate(modules):
        n_off, n_len = name_entries[i]
        c_off, c_len = content_entries[i]
        # 52 bytes = 13 x u32: name(2) + content(2) + sourcemap(2) +
        # bytecode(2) + moduleInfo(2) + bytecodeOriginPath(2) + flags(1)
        entry = struct.pack(
            "<IIIIIIIIIIIII",
            n_off, n_len,
            c_off, c_len,
            0, 0,  # sourcemap
            0, 0,  # bytecode
            0, 0,  # moduleInfo
            0, 0,  # bytecodeOriginPath
            0,     # flags
        )
        module_table += entry

    blob_parts.append(module_table)
    modules_offset = current_offset
    current_offset += len(module_table)

    data_blob = b"".join(blob_parts)
    byte_count = len(data_blob)

    # OFFSETS struct (32 bytes): u64 byteCount, u32 modOff, u32 modLen,
    # u32 entryPointId, u32 execArgvOff, u32 execArgvLen, u32 flags
    offsets = struct.pack(
        "<QIIIIII",
        byte_count,
        modules_offset, len(module_table),
        0,  # entryPointId
        0, 0,  # compileExecArgvPtr
        0,  # flags
    )

    # Total file
    total_byte_count = len(elf_stub) + len(data_blob) + len(offsets) + len(_BUN_MARKER) + 8
    file_content = (
        elf_stub
        + data_blob
        + offsets
        + _BUN_MARKER
        + struct.pack("<Q", total_byte_count)
    )

    path.write_bytes(file_content)
    return path


class TestListModules:
    def test_single_module(self, tmp_path):
        content = b"console.log('hello');"
        path = _build_fake_sea(tmp_path, [("cli.js", content)])
        modules = list_modules(path)
        assert len(modules) == 1
        assert modules[0].name == "cli.js"
        assert modules[0].content_length == len(content)

    def test_multiple_modules(self, tmp_path):
        mods = [
            ("/$bunfs/root/src/entrypoints/cli.js", b"main code"),
            ("/$bunfs/root/resvg.wasm", b"\x00wasm"),
            ("/$bunfs/root/tree-sitter.js", b"ts code"),
        ]
        path = _build_fake_sea(tmp_path, mods)
        modules = list_modules(path)
        assert len(modules) == 3
        assert modules[0].name == "/$bunfs/root/src/entrypoints/cli.js"
        assert modules[1].name == "/$bunfs/root/resvg.wasm"
        assert modules[2].name == "/$bunfs/root/tree-sitter.js"

    def test_empty_file_raises(self, tmp_path):
        path = tmp_path / "empty"
        path.write_bytes(b"")
        with pytest.raises(BunSEAError, match="too small"):
            list_modules(path)

    def test_no_marker_raises(self, tmp_path):
        path = tmp_path / "notbun"
        path.write_bytes(b"\x00" * 200)
        with pytest.raises(BunSEAError, match="marker not found"):
            list_modules(path)


class TestExtractModule:
    def test_extract_by_suffix(self, tmp_path):
        content = b"// cli code"
        path = _build_fake_sea(tmp_path, [
            ("/$bunfs/root/src/entrypoints/cli.js", content),
            ("/$bunfs/root/other.js", b"other"),
        ])
        result = extract_module(path, "cli.js")
        assert result == content

    def test_missing_module_raises(self, tmp_path):
        path = _build_fake_sea(tmp_path, [("other.js", b"code")])
        with pytest.raises(BunSEAError, match="not found"):
            extract_module(path, "cli.js")


class TestExtractCliJs:
    def test_extract(self, tmp_path):
        content = b"// @bun @bytecode\nconsole.log('hi');"
        path = _build_fake_sea(tmp_path, [
            ("/$bunfs/root/src/entrypoints/cli.js", content),
        ])
        assert extract_cli_js(path) == content


class TestCliJsHash:
    def test_hash(self, tmp_path):
        content = b"console.log('test');"
        path = _build_fake_sea(tmp_path, [("cli.js", content)])
        expected = hashlib.sha256(content).hexdigest()
        assert cli_js_hash(path) == expected


class TestRealBinary:
    """Tests against the actual Claude Code binary (if available)."""

    CLAUDE_PATH = Path("/home/agent/.local/share/claude/versions/2.1.71")

    @pytest.mark.skipif(
        not Path("/home/agent/.local/share/claude/versions/2.1.71").exists(),
        reason="Claude binary not available",
    )
    def test_list_real_modules(self):
        modules = list_modules(self.CLAUDE_PATH)
        assert len(modules) == 15
        names = [m.name for m in modules]
        assert any("cli.js" in n for n in names)

    @pytest.mark.skipif(
        not Path("/home/agent/.local/share/claude/versions/2.1.71").exists(),
        reason="Claude binary not available",
    )
    def test_extract_real_cli_js(self):
        content = extract_cli_js(self.CLAUDE_PATH)
        assert len(content) > 10_000_000  # ~11.5MB
        assert content[:10] == b"// @bun @b"
        assert b"\x00" not in content  # plain JS, no null bytes

    @pytest.mark.skipif(
        not Path("/home/agent/.local/share/claude/versions/2.1.71").exists(),
        reason="Claude binary not available",
    )
    def test_real_hash_deterministic(self):
        h1 = cli_js_hash(self.CLAUDE_PATH)
        h2 = cli_js_hash(self.CLAUDE_PATH)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex
