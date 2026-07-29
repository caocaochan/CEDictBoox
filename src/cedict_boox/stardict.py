"""StarDict 2.4.2 writer for BOOX-compatible dictionary files."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cmp_to_key
from pathlib import Path
import struct
from typing import Mapping


BASE_NAME = "cc-cedict-boox"
PACKAGE_DIR_NAME = "CC-CEDICT-Boox"
UINT32_MAX = (1 << 32) - 1


class StarDictError(ValueError):
    """Raised when dictionary data cannot be represented in StarDict."""


@dataclass(frozen=True, slots=True)
class StarDictStats:
    wordcount: int
    idx_size: int
    dict_size: int


def stardict_compare(left: str | bytes, right: str | bytes) -> int:
    """Implement stardict_strcmp: ASCII case-insensitive, then bytewise."""
    left_bytes = left.encode("utf-8") if isinstance(left, str) else left
    right_bytes = right.encode("utf-8") if isinstance(right, str) else right
    folded_left = _ascii_lower(left_bytes)
    folded_right = _ascii_lower(right_bytes)
    if folded_left < folded_right:
        return -1
    if folded_left > folded_right:
        return 1
    if left_bytes < right_bytes:
        return -1
    if left_bytes > right_bytes:
        return 1
    return 0


def sorted_headwords(words: Mapping[str, str] | list[str]) -> list[str]:
    values = list(words)
    return sorted(values, key=cmp_to_key(stardict_compare))


def write_stardict(
    output_dir: Path,
    articles: Mapping[str, str],
    *,
    source_date: str,
) -> StarDictStats:
    if not articles:
        raise StarDictError("cannot write an empty dictionary")
    output_dir.mkdir(parents=True, exist_ok=True)

    idx = bytearray()
    dictionary = bytearray()
    for word in sorted_headwords(list(articles)):
        word_bytes = word.encode("utf-8")
        if not word_bytes or len(word_bytes) >= 256 or b"\x00" in word_bytes:
            raise StarDictError(f"invalid StarDict headword {word!r}")
        article_bytes = articles[word].encode("utf-8")
        if not article_bytes:
            raise StarDictError(f"empty article for {word!r}")
        offset = len(dictionary)
        size = len(article_bytes)
        if offset > UINT32_MAX or size > UINT32_MAX or offset + size > UINT32_MAX:
            raise StarDictError("dictionary exceeds StarDict 2.4.2 32-bit limits")
        dictionary.extend(article_bytes)
        idx.extend(word_bytes)
        idx.append(0)
        idx.extend(struct.pack("!II", offset, size))

    date_value = source_date.replace("-", ".")
    description = (
        "CC-CEDICT Chinese-English data, adapted for BOOX StarDict lookup."
        "<br>CC-CEDICT contributors; distributed by MDBG."
        "<br>Data license: CC BY-SA 4.0 "
        "(https://creativecommons.org/licenses/by-sa/4.0/)."
    )
    ifo = (
        "StarDict's dict ifo file\n"
        "version=2.4.2\n"
        "bookname=CC-CEDICT Chinese-English (Simplified + Traditional)\n"
        f"wordcount={len(articles)}\n"
        f"idxfilesize={len(idx)}\n"
        "author=CC-CEDICT contributors\n"
        "website=https://www.mdbg.net/chinese/dictionary?page=cc-cedict\n"
        f"description={description}\n"
        f"date={date_value}\n"
        "sametypesequence=h\n"
    ).encode("utf-8")

    (output_dir / f"{BASE_NAME}.idx").write_bytes(idx)
    (output_dir / f"{BASE_NAME}.dict").write_bytes(dictionary)
    (output_dir / f"{BASE_NAME}.ifo").write_bytes(ifo)
    return StarDictStats(
        wordcount=len(articles), idx_size=len(idx), dict_size=len(dictionary)
    )


def _ascii_lower(value: bytes) -> bytes:
    return bytes(byte + 32 if 65 <= byte <= 90 else byte for byte in value)

