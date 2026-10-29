"""Read and write .hbr record streams.

An .hbr stream is a sequence of Envelope messages, each prefixed with its byte length as a protobuf
varint32. Chunk files (.hbr.zst) are that stream compressed with zstd. This mirrors
hb.common.io.HbrWriter / HbrReader in the Java mods.

A chunk that was open when the game crashed ends mid-frame or mid-record. Readers raise on that by
default; pass ``partial=True`` to keep every complete record before the cut.
"""

from __future__ import annotations

import io
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import BinaryIO

import zstandard

from hbpipe.schema import pb


def iter_records(stream: BinaryIO, *, partial: bool = False) -> Iterator[bytes]:
    """Yields the serialized bytes of every record in an uncompressed .hbr stream."""
    while True:
        try:
            size = _read_varint(stream)
        except EOFError:
            if partial:
                return
            raise
        if size is None:
            return
        data = stream.read(size)
        if len(data) != size:
            if partial:
                return
            raise EOFError(f"truncated record: expected {size} bytes, got {len(data)}")
        yield data


def iter_envelopes(stream: BinaryIO, *, partial: bool = False) -> Iterator[pb.Envelope]:
    """Yields every record in an uncompressed .hbr stream."""
    for data in iter_records(stream, partial=partial):
        yield pb.Envelope.FromString(data)


def decode(data: bytes, *, partial: bool = False) -> list[pb.Envelope]:
    return list(iter_envelopes(io.BytesIO(data), partial=partial))


def encode(envelopes: Iterable[pb.Envelope]) -> bytes:
    out = bytearray()
    for envelope in envelopes:
        body = envelope.SerializeToString()
        out += _encode_varint(len(body))
        out += body
    return bytes(out)


def decompress(data: bytes, *, partial: bool = False) -> bytes:
    """Decompresses a .hbr.zst chunk, including chunks made of several concatenated frames.

    Raises EOFError on a frame cut off by a crash; with ``partial``, returns whatever decompressed
    before the cut instead.
    """
    out = bytearray()
    remaining = data
    while remaining:
        decompressor = zstandard.ZstdDecompressor().decompressobj()
        try:
            out += decompressor.decompress(remaining)
        except zstandard.ZstdError:
            if partial:
                break
            raise
        if not decompressor.eof:
            if partial:
                break
            raise EOFError("truncated zstd frame")
        remaining = decompressor.unused_data
    return bytes(out)


def read_file(path: str | Path, *, partial: bool = False) -> list[pb.Envelope]:
    """Reads a .hbr or .hbr.zst file."""
    return decode(read_bytes(path, partial=partial), partial=partial)


def read_bytes(path: str | Path, *, partial: bool = False) -> bytes:
    """The uncompressed .hbr stream of a .hbr or .hbr.zst file."""
    path = Path(path)
    data = path.read_bytes()
    return decompress(data, partial=partial) if path.suffix == ".zst" else data


def _read_varint(stream: BinaryIO) -> int | None:
    result = 0
    shift = 0
    while True:
        b = stream.read(1)
        if not b:
            if shift == 0:
                return None
            raise EOFError("truncated length prefix")
        result |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            return result
        shift += 7
        if shift > 28:
            raise ValueError("length prefix exceeds varint32")


def _encode_varint(value: int) -> bytes:
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)
