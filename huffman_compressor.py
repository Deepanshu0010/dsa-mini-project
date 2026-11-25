#!/usr/bin/env python3
"""Losslessly compress and decompress files with Huffman coding.

The archive stores the original byte length, encoded bit length, and a
preorder-serialized Huffman tree, followed by a packed bit stream. A raw mode
is used when Huffman coding would make the payload larger.
"""

from __future__ import annotations

import argparse
import heapq
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


MAGIC = b"DSAHUF1\0"
VERSION = 1
MODE_HUFFMAN = 0
MODE_RAW = 1
# magic, version, mode, original byte count, encoded bit count
HEADER = struct.Struct(">8sBBQQ")


@dataclass
class Node:
    frequency: int
    symbol: int | None = None
    left: "Node | None" = None
    right: "Node | None" = None

    @property
    def is_leaf(self) -> bool:
        return self.symbol is not None


def build_tree(data: bytes) -> Node | None:
    """Build a deterministic Huffman tree from byte frequencies."""
    frequencies = [0] * 256
    for value in data:
        frequencies[value] += 1

    heap: list[tuple[int, int, Node]] = []
    order = 0
    for symbol, frequency in enumerate(frequencies):
        if frequency:
            heapq.heappush(heap, (frequency, order, Node(frequency, symbol)))
            order += 1

    if not heap:
        return None

    while len(heap) > 1:
        left_frequency, _, left = heapq.heappop(heap)
        right_frequency, _, right = heapq.heappop(heap)
        parent = Node(left_frequency + right_frequency, left=left, right=right)
        heapq.heappush(heap, (parent.frequency, order, parent))
        order += 1
    return heap[0][2]


def make_codes(root: Node | None) -> dict[int, tuple[int, int]]:
    """Return symbol -> (code bits, code length)."""
    if root is None:
        return {}
    codes: dict[int, tuple[int, int]] = {}

    def visit(node: Node, code: int, length: int) -> None:
        if node.is_leaf:
            # A one-symbol input still needs one bit per input byte.
            codes[node.symbol] = (code, max(length, 1))  # type: ignore[index]
            return
        assert node.left is not None and node.right is not None
        visit(node.left, code << 1, length + 1)
        visit(node.right, (code << 1) | 1, length + 1)

    visit(root, 0, 0)
    return codes


def serialize_tree(root: Node | None) -> bytes:
    """Encode a tree in preorder: 0=branch, 1=leaf followed by its byte."""
    if root is None:
        return b""
    result = bytearray()

    def write(node: Node) -> None:
        if node.is_leaf:
            result.extend((1, node.symbol))  # type: ignore[arg-type]
        else:
            result.append(0)
            assert node.left is not None and node.right is not None
            write(node.left)
            write(node.right)

    write(root)
    return bytes(result)


class BitWriter:
    def __init__(self, output: BinaryIO) -> None:
        self.output = output
        self.byte = 0
        self.used = 0
        self.bit_count = 0

    def write(self, code: int, length: int) -> None:
        for shift in range(length - 1, -1, -1):
            self.byte = (self.byte << 1) | ((code >> shift) & 1)
            self.used += 1
            self.bit_count += 1
            if self.used == 8:
                self.output.write(bytes((self.byte,)))
                self.byte = 0
                self.used = 0

    def finish(self) -> None:
        if self.used:
            self.byte <<= 8 - self.used
            self.output.write(bytes((self.byte,)))
            self.byte = 0
            self.used = 0


class BitReader:
    def __init__(self, source: BinaryIO, bit_count: int) -> None:
        self.source = source
        self.remaining = bit_count
        self.byte = 0
        self.available = 0

    def read_bit(self) -> int:
        if self.remaining <= 0:
            raise ValueError("Archive ended before all encoded bits were read")
        if self.available == 0:
            raw = self.source.read(1)
            if not raw:
                raise ValueError("Archive is truncated in the compressed data")
            self.byte = raw[0]
            self.available = 8
        bit = (self.byte >> (self.available - 1)) & 1
        self.available -= 1
        self.remaining -= 1
        return bit


def write_header(output: BinaryIO, mode: int, original_size: int, bit_count: int) -> None:
    output.write(HEADER.pack(MAGIC, VERSION, mode, original_size, bit_count))


def compress_file(source_path: Path, archive_path: Path) -> tuple[int, int, str]:
    data = source_path.read_bytes()
    root = build_tree(data)
    codes = make_codes(root)
    tree_bytes = serialize_tree(root)
    huffman_bits = sum(codes[value][1] * count for value, count in _frequencies(data).items())
    huffman_size = HEADER.size + len(tree_bytes) + (huffman_bits + 7) // 8
    raw_size = HEADER.size + len(data)

    # Pick the smaller payload representation. Empty files use Huffman mode
    # with no tree and no data bits.
    mode = MODE_RAW if data and raw_size <= huffman_size else MODE_HUFFMAN
    temporary_path = archive_path.with_name(archive_path.name + ".tmp")
    try:
        with temporary_path.open("wb") as output:
            if mode == MODE_RAW:
                write_header(output, MODE_RAW, len(data), len(data) * 8)
                output.write(data)
            else:
                write_header(output, MODE_HUFFMAN, len(data), huffman_bits)
                output.write(tree_bytes)
                if root is not None:
                    writer = BitWriter(output)
                    for value in data:
                        code, length = codes[value]
                        writer.write(code, length)
                    writer.finish()
        os.replace(temporary_path, archive_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return len(data), archive_path.stat().st_size, "raw" if mode == MODE_RAW else "Huffman"


def _frequencies(data: bytes) -> dict[int, int]:
    counts: dict[int, int] = {}
    for value in data:
        counts[value] = counts.get(value, 0) + 1
    return counts


def read_tree(source: BinaryIO, original_size: int) -> Node | None:
    """Read and validate the serialized tree from an archive."""
    if original_size == 0:
        return None
    seen: set[int] = set()
    nodes = 0

    def read_node(depth: int = 0) -> Node:
        nonlocal nodes
        nodes += 1
        if nodes > 511 or depth > 255:
            raise ValueError("Invalid Huffman tree: too many nodes or excessive depth")
        marker = source.read(1)
        if not marker:
            raise ValueError("Archive is truncated in the Huffman tree")
        if marker == b"\x01":
            raw_symbol = source.read(1)
            if not raw_symbol:
                raise ValueError("Archive is truncated in a Huffman leaf")
            symbol = raw_symbol[0]
            if symbol in seen:
                raise ValueError("Invalid Huffman tree: duplicate symbol")
            seen.add(symbol)
            return Node(0, symbol)
        if marker == b"\x00":
            left = read_node(depth + 1)
            right = read_node(depth + 1)
            return Node(0, left=left, right=right)
        raise ValueError("Invalid Huffman tree marker")

    return read_node()


def decompress_file(archive_path: Path, output_path: Path) -> tuple[int, int]:
    temporary_path = output_path.with_name(output_path.name + ".tmp")
    try:
        with archive_path.open("rb") as source:
            raw_header = source.read(HEADER.size)
            if len(raw_header) != HEADER.size:
                raise ValueError("File is too short to be a DSA Huffman archive")
            magic, version, mode, original_size, bit_count = HEADER.unpack(raw_header)
            if magic != MAGIC or version != VERSION:
                raise ValueError("Not a supported DSA Huffman archive")
            if original_size > 0x7FFFFFFFFFFFFFFF:
                raise ValueError("Declared output size is too large")

            with temporary_path.open("wb") as output:
                if mode == MODE_RAW:
                    if bit_count != original_size * 8:
                        raise ValueError("Invalid raw archive bit count")
                    remaining = original_size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            raise ValueError("Archive is truncated in raw data")
                        output.write(chunk)
                        remaining -= len(chunk)
                    if source.read(1):
                        raise ValueError("Archive has unexpected trailing data")
                elif mode == MODE_HUFFMAN:
                    root = read_tree(source, original_size)
                    payload_start = source.tell()
                    if original_size == 0:
                        if bit_count != 0:
                            raise ValueError("Invalid empty archive bit count")
                    elif root is None:
                        raise ValueError("Missing Huffman tree")
                    elif root.is_leaf:
                        if bit_count != original_size:
                            raise ValueError("Invalid single-symbol archive bit count")
                        reader = BitReader(source, bit_count)
                        block = bytes((root.symbol,)) * min(original_size, 1024 * 1024)  # type: ignore[arg-type]
                        remaining = original_size
                        while remaining:
                            amount = min(remaining, len(block))
                            # Consume and validate every bit (all must be zero).
                            for _ in range(amount):
                                if reader.read_bit() != 0:
                                    raise ValueError("Invalid bit for single-symbol Huffman tree")
                            output.write(block[:amount])
                            remaining -= amount
                    else:
                        reader = BitReader(source, bit_count)
                        decoded = 0
                        buffer = bytearray()
                        while decoded < original_size:
                            node = root
                            while not node.is_leaf:
                                bit = reader.read_bit()
                                node = node.right if bit else node.left  # type: ignore[assignment]
                                if node is None:
                                    raise ValueError("Invalid Huffman bit stream")
                            buffer.append(node.symbol)  # type: ignore[arg-type]
                            decoded += 1
                            if len(buffer) >= 65536:
                                output.write(buffer)
                                buffer.clear()
                        if buffer:
                            output.write(buffer)
                        if reader.remaining != 0:
                            raise ValueError("Archive contains unused encoded bits")

                    expected_payload = (bit_count + 7) // 8
                    # The reader fetched full bytes; for empty streams no bytes
                    # were needed. Validate padding and reject appended bytes.
                    payload_end = source.tell()
                    if payload_end - payload_start != expected_payload:
                        raise ValueError("Archive has an incorrect compressed data length")
                    source.seek(0, os.SEEK_END)
                    if source.tell() != payload_end:
                        raise ValueError("Archive has unexpected trailing data")
                    if bit_count % 8 and expected_payload:
                        source.seek(payload_end - 1)
                        last_byte = source.read(1)[0]
                        padding = 8 - (bit_count % 8)
                        if last_byte & ((1 << padding) - 1):
                            raise ValueError("Invalid nonzero padding bits")
                else:
                    raise ValueError("Unknown archive compression mode")
        os.replace(temporary_path, output_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return original_size, output_path.stat().st_size


def default_archive_path(path: Path) -> Path:
    return path.with_name(path.name + ".huf")


def default_output_path(path: Path) -> Path:
    if path.name.lower().endswith(".huf"):
        return path.with_name(path.name[:-4])
    return path.with_name(path.name + ".out")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Losslessly compress or restore files using Huffman coding."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    compress_parser = subparsers.add_parser("compress", help="compress a file")
    compress_parser.add_argument("input", type=Path, help="input text or binary file")
    compress_parser.add_argument("-o", "--output", type=Path, help="archive path (default: INPUT.huf)")

    decompress_parser = subparsers.add_parser("decompress", help="restore a .huf archive")
    decompress_parser.add_argument("input", type=Path, help="archive created by this program")
    decompress_parser.add_argument("-o", "--output", type=Path, help="restored file path (default: archive name without .huf)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source = args.input
    output = args.output or (default_archive_path(source) if args.command == "compress" else default_output_path(source))

    try:
        if not source.is_file():
            raise FileNotFoundError(f"Input file does not exist: {source}")
        if source.resolve() == output.resolve():
            raise ValueError("Input and output paths must be different")
        if args.command == "compress":
            original_size, archive_size, method = compress_file(source, output)
            change = archive_size - original_size
            if original_size:
                ratio = archive_size / original_size * 100
                print(f"Compressed {original_size:,} bytes to {archive_size:,} bytes ({ratio:.1f}% of original; {method} mode).")
            else:
                print(f"Compressed an empty file to {archive_size:,} bytes ({method} mode).")
            if change >= 0 and original_size:
                print(f"The archive is {change:,} bytes larger because the file is small or not compressible.")
            else:
                print(f"Saved {-change:,} bytes.")
        else:
            original_size, restored_size = decompress_file(source, output)
            print(f"Restored {original_size:,} bytes to: {output}")
            if original_size != restored_size:
                raise ValueError("Restored file size does not match the archive")
        print(f"Output: {output}")
        return 0
    except (OSError, ValueError, struct.error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
