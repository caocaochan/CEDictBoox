"""Aggregate CC-CEDICT records and render conservative HTML articles."""

from __future__ import annotations

from collections import defaultdict
from html import escape
from typing import Iterable

from .parser import CedictEntry
from .pinyin import numeric_to_tone_marks


def aggregate_entries(
    entries: Iterable[CedictEntry],
) -> dict[str, tuple[CedictEntry, ...]]:
    grouped: dict[str, list[CedictEntry]] = defaultdict(list)
    seen_by_key: dict[str, set[tuple[str, str, str, tuple[str, ...]]]] = defaultdict(set)

    for entry in entries:
        identity = (
            entry.traditional,
            entry.simplified,
            entry.numeric_pinyin,
            entry.senses,
        )
        keys = (entry.simplified,) if entry.simplified == entry.traditional else (
            entry.simplified,
            entry.traditional,
        )
        for key in keys:
            if identity not in seen_by_key[key]:
                grouped[key].append(entry)
                seen_by_key[key].add(identity)

    return {key: tuple(value) for key, value in grouped.items()}


def render_article(key: str, entries: Iterable[CedictEntry]) -> str:
    records = tuple(entries)
    if not records:
        raise ValueError(f"cannot render empty article for {key!r}")

    rendered_records: list[str] = []
    for entry in records:
        if key == entry.simplified:
            alternate = entry.traditional
        elif key == entry.traditional:
            alternate = entry.simplified
        else:
            raise ValueError(f"entry does not belong to lookup key {key!r}")

        heading = f"<div><b>{escape(key)}</b>"
        if alternate != key:
            heading += f" <span>〔{escape(alternate)}〕</span>"
        pinyin = escape(numeric_to_tone_marks(entry.numeric_pinyin))
        heading += f" <i>{pinyin}</i></div>"
        parts = [heading]
        parts.extend(
            f"<div>{number}. {escape(sense)}</div>"
            for number, sense in enumerate(entry.senses, 1)
        )
        rendered_records.append("".join(parts))
    return "<hr>".join(rendered_records)
