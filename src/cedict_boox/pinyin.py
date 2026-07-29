"""Numeric-pinyin to tone-mark conversion."""

from __future__ import annotations

import re
import unicodedata


class PinyinError(ValueError):
    """Raised when a pinyin token cannot be converted safely."""


_MARKS = {
    "a": "āáǎà",
    "e": "ēéěè",
    "i": "īíǐì",
    "o": "ōóǒò",
    "u": "ūúǔù",
    "ü": "ǖǘǚǜ",
    "A": "ĀÁǍÀ",
    "E": "ĒÉĚÈ",
    "I": "ĪÍǏÌ",
    "O": "ŌÓǑÒ",
    "U": "ŪÚǓÙ",
    "Ü": "ǕǗǙǛ",
}
_PINYIN_TOKEN = re.compile(r"^[A-Za-züÜ:]+[1-5]$")
_COMPOUND_PINYIN_TOKEN = re.compile(r"^(?:[A-Za-züÜ:]+[1-5]){2,}$")
_PINYIN_PART = re.compile(r"[A-Za-züÜ:]+[1-5]")
_UNTONTED_TOKEN = re.compile(r"^[A-Za-z0-9]+$")
_OPAQUE_NUMERIC_TOKEN = re.compile(r"^[0-9]+$")
_PUNCTUATION_TOKEN = re.compile(r"^[,:;.'·\-–—]+$")
_COMBINING_TONES = ("\u0304", "\u0301", "\u030c", "\u0300")


def numeric_to_tone_marks(value: str) -> str:
    """Convert a complete CC-CEDICT V1 pinyin field to tone marks."""
    if not value or "\x00" in value:
        raise PinyinError("pinyin must not be empty or contain NUL")

    tokens = value.split(" ")
    if any(token == "" for token in tokens):
        raise PinyinError(f"invalid repeated or edge whitespace in pinyin {value!r}")

    converted: list[str] = []
    for token in tokens:
        if _PUNCTUATION_TOKEN.fullmatch(token):
            converted.append(token)
            continue
        if _OPAQUE_NUMERIC_TOKEN.fullmatch(token):
            converted.append(token)
            continue
        # CC-CEDICT permits embedded Latin words and acronyms in Chinese
        # headwords; their reading tokens are intentionally not assigned a
        # fabricated Mandarin tone.
        if _UNTONTED_TOKEN.fullmatch(token) and not token[-1:].isdigit():
            converted.append(token)
            continue
        # A small number of verified legacy V1 records concatenate syllables
        # (for example, "shi2ke4"). Preserve that upstream grouping while
        # applying the tone mark to each unambiguous numeric part.
        if _COMPOUND_PINYIN_TOKEN.fullmatch(token):
            converted.append(
                "".join(_convert_syllable(part) for part in _PINYIN_PART.findall(token))
            )
            continue
        if not _PINYIN_TOKEN.fullmatch(token):
            raise PinyinError(f"invalid numeric pinyin token {token!r}")
        if token == "r5" and converted and not _PUNCTUATION_TOKEN.fullmatch(
            converted[-1]
        ):
            converted[-1] += "r"
            continue
        converted.append(_convert_syllable(token))

    return unicodedata.normalize("NFC", " ".join(converted))


def _convert_syllable(token: str) -> str:
    tone = int(token[-1])
    syllable = token[:-1].replace("u:", "ü").replace("U:", "Ü")
    if ":" in syllable:
        raise PinyinError(f"invalid colon placement in pinyin token {token!r}")
    if not syllable:
        raise PinyinError(f"missing syllable in pinyin token {token!r}")
    if tone == 5:
        return syllable

    index = _tone_vowel_index(syllable)
    if index is None:
        syllabic = next(
            (i for i in range(len(syllable) - 1, -1, -1) if syllable[i].lower() in "mn"),
            None,
        )
        if syllabic is None:
            raise PinyinError(f"no markable vowel in pinyin token {token!r}")
        return (
            syllable[: syllabic + 1]
            + _COMBINING_TONES[tone - 1]
            + syllable[syllabic + 1 :]
        )
    vowel = syllable[index]
    marked = _MARKS[vowel][tone - 1]
    return syllable[:index] + marked + syllable[index + 1 :]


def _tone_vowel_index(syllable: str) -> int | None:
    lower = syllable.lower()
    if "a" in lower:
        return lower.index("a")
    if "e" in lower:
        return lower.index("e")
    if "ou" in lower:
        return lower.index("o")
    indices = [i for i, char in enumerate(lower) if char in "iouü"]
    return indices[-1] if indices else None
