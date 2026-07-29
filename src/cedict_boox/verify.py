"""Independent structural verifier for generated StarDict packages."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import struct
import tempfile
from zipfile import BadZipFile, ZipFile

from .stardict import BASE_NAME, PACKAGE_DIR_NAME, stardict_compare


class VerificationError(ValueError):
    """Raised when a generated dictionary package is invalid."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    wordcount: int
    idx_size: int
    dict_size: int


_REQUIRED_PACKAGE_FILES = {
    f"{BASE_NAME}.ifo",
    f"{BASE_NAME}.idx",
    f"{BASE_NAME}.dict",
    "README.txt",
    "SOURCE.json",
    "LICENSE-CC-BY-SA-4.0.txt",
}


def verify_package(path: Path) -> VerificationResult:
    path = path.resolve()
    if path.is_dir():
        return verify_directory(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        return verify_zip(path)
    raise VerificationError(f"{path}: expected a dictionary directory or ZIP")


def verify_zip(path: Path) -> VerificationResult:
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos if not info.is_dir()]
            if len(names) != len(set(names)):
                raise VerificationError(f"{path}: duplicate ZIP member")
            expected = {
                f"{PACKAGE_DIR_NAME}/{name}" for name in _REQUIRED_PACKAGE_FILES
            }
            if set(names) != expected:
                missing = sorted(expected - set(names))
                extra = sorted(set(names) - expected)
                raise VerificationError(
                    f"{path}: invalid ZIP contents; missing={missing}, extra={extra}"
                )
            for name in names:
                pure = PurePosixPath(name)
                if name.startswith("/") or ".." in pure.parts:
                    raise VerificationError(f"{path}: unsafe ZIP member {name!r}")
            with tempfile.TemporaryDirectory(prefix="cedict-boox-verify-") as temp:
                archive.extractall(temp)
                return verify_directory(Path(temp) / PACKAGE_DIR_NAME)
    except BadZipFile as exc:
        raise VerificationError(f"{path}: invalid ZIP archive") from exc


def verify_directory(path: Path) -> VerificationResult:
    if not path.is_dir():
        raise VerificationError(f"{path}: dictionary directory does not exist")
    actual = {item.name for item in path.iterdir() if item.is_file()}
    if actual != _REQUIRED_PACKAGE_FILES:
        raise VerificationError(
            f"{path}: invalid package files; "
            f"missing={sorted(_REQUIRED_PACKAGE_FILES - actual)}, "
            f"extra={sorted(actual - _REQUIRED_PACKAGE_FILES)}"
        )
    if any(item.is_dir() for item in path.iterdir()):
        raise VerificationError(f"{path}: unexpected subdirectory")

    ifo_path = path / f"{BASE_NAME}.ifo"
    idx_path = path / f"{BASE_NAME}.idx"
    dict_path = path / f"{BASE_NAME}.dict"
    ifo_bytes = ifo_path.read_bytes()
    idx_bytes = idx_path.read_bytes()
    dict_bytes = dict_path.read_bytes()
    ifo = _parse_ifo(ifo_bytes, ifo_path)

    if ifo.get("version") != "2.4.2":
        raise VerificationError(f"{ifo_path}: version must be 2.4.2")
    if ifo.get("sametypesequence") != "h":
        raise VerificationError(f"{ifo_path}: sametypesequence must be h")
    if "idxoffsetbits" in ifo:
        raise VerificationError(f"{ifo_path}: 64-bit offsets are not allowed")
    try:
        expected_words = int(ifo["wordcount"])
        expected_idx_size = int(ifo["idxfilesize"])
    except (KeyError, ValueError) as exc:
        raise VerificationError(f"{ifo_path}: invalid required count field") from exc
    if expected_idx_size != len(idx_bytes):
        raise VerificationError(
            f"{ifo_path}: idxfilesize={expected_idx_size}, actual={len(idx_bytes)}"
        )

    records = _parse_idx(idx_bytes, idx_path)
    if len(records) != expected_words:
        raise VerificationError(
            f"{ifo_path}: wordcount={expected_words}, actual={len(records)}"
        )

    previous_word: str | None = None
    expected_offset = 0
    for word, offset, size, record_offset in records:
        if previous_word is not None and stardict_compare(previous_word, word) >= 0:
            raise VerificationError(
                f"{idx_path}:{record_offset}: keys are duplicate or incorrectly sorted"
            )
        previous_word = word
        if offset != expected_offset:
            raise VerificationError(
                f"{idx_path}:{record_offset}: non-contiguous article offset "
                f"{offset}, expected {expected_offset}"
            )
        end = offset + size
        if end > len(dict_bytes):
            raise VerificationError(
                f"{idx_path}:{record_offset}: article extends beyond .dict"
            )
        try:
            article = dict_bytes[offset:end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VerificationError(
                f"{dict_path}:{offset}: article is not valid UTF-8"
            ) from exc
        if not article:
            raise VerificationError(f"{dict_path}:{offset}: empty article")
        _verify_html(article, dict_path, offset)
        expected_offset = end
    if expected_offset != len(dict_bytes):
        raise VerificationError(f"{dict_path}: unreferenced trailing bytes")

    try:
        json.loads((path / "SOURCE.json").read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{path / 'SOURCE.json'}: invalid JSON") from exc
    for text_name in ("README.txt", "LICENSE-CC-BY-SA-4.0.txt"):
        try:
            (path / text_name).read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise VerificationError(f"{path / text_name}: invalid UTF-8") from exc

    return VerificationResult(
        wordcount=len(records), idx_size=len(idx_bytes), dict_size=len(dict_bytes)
    )


def _parse_ifo(data: bytes, path: Path) -> dict[str, str]:
    if not data.endswith(b"\n"):
        raise VerificationError(f"{path}: missing final newline")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{path}: invalid UTF-8") from exc
    if text.startswith("\ufeff"):
        raise VerificationError(f"{path}: UTF-8 BOM is not allowed")
    lines = text.splitlines()
    if not lines or lines[0] != "StarDict's dict ifo file":
        raise VerificationError(f"{path}: invalid StarDict magic line")
    values: dict[str, str] = {}
    for number, line in enumerate(lines[1:], 2):
        if not line or "=" not in line:
            raise VerificationError(f"{path}:{number}: invalid key/value line")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise VerificationError(f"{path}:{number}: duplicate or empty key")
        values[key] = value
    if list(values)[:1] != ["version"]:
        raise VerificationError(f"{path}: version must be the first option")
    return values


def _parse_idx(data: bytes, path: Path) -> list[tuple[str, int, int, int]]:
    records: list[tuple[str, int, int, int]] = []
    cursor = 0
    while cursor < len(data):
        record_offset = cursor
        terminator = data.find(b"\x00", cursor)
        if terminator < 0:
            raise VerificationError(f"{path}:{cursor}: unterminated headword")
        word_bytes = data[cursor:terminator]
        if not word_bytes or len(word_bytes) >= 256:
            raise VerificationError(f"{path}:{cursor}: invalid headword length")
        cursor = terminator + 1
        if cursor + 8 > len(data):
            raise VerificationError(f"{path}:{cursor}: truncated offset/size")
        offset, size = struct.unpack("!II", data[cursor : cursor + 8])
        cursor += 8
        try:
            word = word_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VerificationError(f"{path}:{record_offset}: invalid UTF-8 key") from exc
        records.append((word, offset, size, record_offset))
    return records


class _ArticleHTMLParser(HTMLParser):
    allowed = {"div", "b", "span", "i", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag not in self.allowed:
            raise VerificationError(f"disallowed HTML tag <{tag}>")
        if attrs:
            raise VerificationError(f"HTML attributes are not allowed on <{tag}>")
        if tag != "hr":
            self.stack.append(tag)

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag != "hr" or attrs:
            raise VerificationError(f"disallowed self-closing HTML tag <{tag}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "hr" or not self.stack or self.stack[-1] != tag:
            raise VerificationError(f"unbalanced HTML closing tag </{tag}>")
        self.stack.pop()


def _verify_html(article: str, path: Path, offset: int) -> None:
    parser = _ArticleHTMLParser()
    try:
        parser.feed(article)
        parser.close()
    except VerificationError as exc:
        raise VerificationError(f"{path}:{offset}: {exc}") from exc
    if parser.stack:
        raise VerificationError(
            f"{path}:{offset}: unclosed HTML tag <{parser.stack[-1]}>"
        )

