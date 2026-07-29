"""Strict parser for the stable CC-CEDICT V1 text format."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping


class CedictParseError(ValueError):
    """Raised when a CC-CEDICT source cannot be parsed without data loss."""


@dataclass(frozen=True, slots=True)
class CedictEntry:
    traditional: str
    simplified: str
    numeric_pinyin: str
    senses: tuple[str, ...]
    source_line: int


@dataclass(frozen=True, slots=True)
class CedictDocument:
    entries: tuple[CedictEntry, ...]
    metadata: Mapping[str, str]


_ENTRY_RE = re.compile(
    r"^(?P<traditional>\S+) (?P<simplified>\S+) "
    r"\[(?P<pinyin>[^\[\]]+)\] /(?P<senses>.*)/$"
)
_META_RE = re.compile(r"^#!\s*([A-Za-z][A-Za-z0-9_-]*)\s*=\s*(.*?)\s*$")


def parse_cedict_bytes(data: bytes, source_name: str = "<bytes>") -> CedictDocument:
    """Decode and strictly parse a CC-CEDICT V1 byte stream."""
    if b"\x00" in data:
        raise CedictParseError(f"{source_name}: contains a NUL byte")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CedictParseError(
            f"{source_name}:{exc.start}: invalid UTF-8: {exc.reason}"
        ) from exc
    return parse_cedict_text(text, source_name)


def parse_cedict_text(text: str, source_name: str = "<text>") -> CedictDocument:
    metadata: dict[str, str] = {}
    entries: list[CedictEntry] = []
    seen: dict[tuple[str, str, str], int] = {}

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.rstrip("\r")
        if not line:
            continue
        if line.startswith("#"):
            match = _META_RE.match(line)
            if match:
                metadata[match.group(1).lower()] = match.group(2)
            continue

        match = _ENTRY_RE.fullmatch(line)
        if not match:
            raise CedictParseError(
                f"{source_name}:{line_number}: invalid CC-CEDICT V1 entry"
            )

        traditional = match.group("traditional")
        simplified = match.group("simplified")
        pinyin = match.group("pinyin")
        raw_senses = match.group("senses")

        if not traditional or not simplified:
            raise CedictParseError(
                f"{source_name}:{line_number}: headwords must not be empty"
            )
        for label, word in (
            ("traditional", traditional),
            ("simplified", simplified),
        ):
            encoded = word.encode("utf-8")
            if len(encoded) >= 256:
                raise CedictParseError(
                    f"{source_name}:{line_number}: {label} headword is "
                    f"{len(encoded)} UTF-8 bytes; StarDict requires fewer than 256"
                )
        if not pinyin.strip():
            raise CedictParseError(
                f"{source_name}:{line_number}: pinyin must not be empty"
            )

        senses = tuple(raw_senses.split("/"))
        if not senses or any(not sense for sense in senses):
            raise CedictParseError(
                f"{source_name}:{line_number}: definitions must not contain "
                "empty senses"
            )

        identity = (traditional, simplified, pinyin)
        if identity in seen:
            raise CedictParseError(
                f"{source_name}:{line_number}: duplicate traditional/"
                f"simplified/pinyin record (first seen on line {seen[identity]})"
            )
        seen[identity] = line_number
        entries.append(
            CedictEntry(
                traditional=traditional,
                simplified=simplified,
                numeric_pinyin=pinyin,
                senses=senses,
                source_line=line_number,
            )
        )

    if not entries:
        raise CedictParseError(f"{source_name}: contains no CC-CEDICT entries")

    declared = metadata.get("entries")
    if declared is not None:
        try:
            declared_count = int(declared)
        except ValueError as exc:
            raise CedictParseError(
                f"{source_name}: invalid declared entry count {declared!r}"
            ) from exc
        if declared_count != len(entries):
            raise CedictParseError(
                f"{source_name}: header declares {declared_count} entries, "
                f"but {len(entries)} were parsed"
            )

    version = metadata.get("version")
    if version is not None and not version.startswith("1"):
        raise CedictParseError(
            f"{source_name}: unsupported CC-CEDICT version {version!r}; "
            "only stable V1 exports are accepted"
        )

    return CedictDocument(entries=tuple(entries), metadata=metadata)

