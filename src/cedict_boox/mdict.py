"""Deterministic MDict 2.0 writer for BOOX-compatible MDX files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path
import struct
from typing import Callable, Mapping, Sequence, TypeVar
import zlib


MDX_VERSION = "2.0"
KEY_BLOCK_SIZE = 32 * 1024
RECORD_BLOCK_SIZE = 64 * 1024
TITLE = "CC-CEDICT Chinese-English (Simplified + Traditional)"
_UINT16_MAX = (1 << 16) - 1
_T = TypeVar("_T")


class MDictError(ValueError):
    """Raised when dictionary data cannot be represented as MDict 2.0."""


@dataclass(frozen=True, slots=True)
class MDictStats:
    wordcount: int
    key_blocks: int
    record_blocks: int
    mdx_size: int


@dataclass(frozen=True, slots=True)
class _Entry:
    key: bytes
    record: bytes
    offset: int


def write_mdict(
    destination: Path,
    articles: Mapping[str, str],
    *,
    source_date: str,
) -> MDictStats:
    """Write an unencrypted, UTF-8, zlib-compressed MDict 2.0 file."""
    if not articles:
        raise MDictError("cannot write an empty dictionary")
    try:
        date.fromisoformat(source_date)
    except ValueError as exc:
        raise MDictError(f"invalid source date {source_date!r}") from exc

    entries: list[_Entry] = []
    offset = 0
    for key, article in sorted(articles.items()):
        key_bytes = key.encode("utf-8")
        article_bytes = article.encode("utf-8")
        if not key_bytes or b"\x00" in key_bytes:
            raise MDictError(f"invalid MDict headword {key!r}")
        if len(key_bytes) > _UINT16_MAX:
            raise MDictError(f"MDict headword is too long: {key!r}")
        if not article_bytes:
            raise MDictError(f"empty article for {key!r}")
        if b"\x00" in article_bytes:
            raise MDictError(f"article contains a NUL byte for {key!r}")
        record = article_bytes + b"\x00"
        entries.append(_Entry(key=key_bytes, record=record, offset=offset))
        offset += len(record)

    key_groups = _split_groups(
        entries, KEY_BLOCK_SIZE, lambda entry: 8 + len(entry.key) + 1
    )
    key_blocks: list[tuple[bytes, int, bytes, bytes, int]] = []
    for group in key_groups:
        raw = b"".join(
            struct.pack(">Q", entry.offset) + entry.key + b"\x00" for entry in group
        )
        compressed = _compress_block(raw)
        key_blocks.append(
            (compressed, len(raw), group[0].key, group[-1].key, len(group))
        )

    key_index_raw = bytearray()
    for compressed, raw_size, first, last, count in key_blocks:
        key_index_raw.extend(struct.pack(">QH", count, len(first)))
        key_index_raw.extend(first)
        key_index_raw.append(0)
        key_index_raw.extend(struct.pack(">H", len(last)))
        key_index_raw.extend(last)
        key_index_raw.append(0)
        key_index_raw.extend(struct.pack(">QQ", len(compressed), raw_size))
    key_index = _compress_block(bytes(key_index_raw))
    key_preamble = struct.pack(
        ">QQQQQ",
        len(key_blocks),
        len(entries),
        len(key_index_raw),
        len(key_index),
        sum(len(block[0]) for block in key_blocks),
    )

    record_groups = _split_groups(
        entries, RECORD_BLOCK_SIZE, lambda entry: len(entry.record)
    )
    record_blocks: list[tuple[bytes, int]] = []
    for group in record_groups:
        raw = b"".join(entry.record for entry in group)
        record_blocks.append((_compress_block(raw), len(raw)))
    record_index = b"".join(
        struct.pack(">QQ", len(compressed), raw_size)
        for compressed, raw_size in record_blocks
    )
    record_preamble = struct.pack(
        ">QQQQ",
        len(record_blocks),
        len(entries),
        len(record_index),
        sum(len(block[0]) for block in record_blocks),
    )

    header = _header_bytes(source_date)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        output.write(struct.pack(">I", len(header)))
        output.write(header)
        output.write(struct.pack("<I", _adler32(header)))
        output.write(key_preamble)
        output.write(struct.pack(">I", _adler32(key_preamble)))
        output.write(key_index)
        for compressed, _, _, _, _ in key_blocks:
            output.write(compressed)
        output.write(record_preamble)
        output.write(record_index)
        for compressed, _ in record_blocks:
            output.write(compressed)

    return MDictStats(
        wordcount=len(entries),
        key_blocks=len(key_blocks),
        record_blocks=len(record_blocks),
        mdx_size=destination.stat().st_size,
    )


def _header_bytes(source_date: str) -> bytes:
    description = (
        "CC-CEDICT Chinese-English data, adapted for BOOX MDict lookup. "
        "CC-CEDICT contributors; distributed by MDBG. "
        "Data license: CC BY-SA 4.0 "
        "(https://creativecommons.org/licenses/by-sa/4.0/)."
    )
    header = (
        '<Dictionary GeneratedByEngineVersion="2.0" '
        'RequiredEngineVersion="2.0" Encrypted="0" Encoding="UTF-8" '
        f'Format="Html" CreationDate="{source_date}" Compact="No" Compat="No" '
        'KeyCaseSensitive="No" StripKey="No" '
        f'Description="{escape(description, quote=True)}" '
        f'Title="{escape(TITLE, quote=True)}" DataSourceFormat="106" '
        'StyleSheet="" RegisterBy="" RegCode=""/>\r\n\x00'
    )
    return header.encode("utf-16le")


def _compress_block(data: bytes) -> bytes:
    return (
        struct.pack("<I", 2)
        + struct.pack(">I", _adler32(data))
        + zlib.compress(data, level=9)
    )


def _adler32(data: bytes) -> int:
    return zlib.adler32(data) & 0xFFFFFFFF


def _split_groups(
    entries: Sequence[_T],
    target_size: int,
    size_of: Callable[[_T], int],
) -> list[list[_T]]:
    groups: list[list[_T]] = []
    current: list[_T] = []
    current_size = 0
    for entry in entries:
        entry_size = size_of(entry)
        if current and current_size + entry_size > target_size:
            groups.append(current)
            current = []
            current_size = 0
        current.append(entry)
        current_size += entry_size
    if current:
        groups.append(current)
    return groups
