"""Independent structural verifier for generated StarDict packages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import struct
import tempfile
import xml.etree.ElementTree as ElementTree
import zlib
from zipfile import BadZipFile, ZipFile

from .mdict import MDX_VERSION
from .stardict import BASE_NAME, PACKAGE_DIR_NAME, stardict_compare


class VerificationError(ValueError):
    """Raised when a generated dictionary package is invalid."""


@dataclass(frozen=True, slots=True)
class VerificationResult:
    wordcount: int
    idx_size: int
    dict_size: int


@dataclass(frozen=True, slots=True)
class MDictVerificationResult:
    wordcount: int
    key_blocks: int
    record_blocks: int
    mdx_size: int


_REQUIRED_PACKAGE_FILES = {
    f"{BASE_NAME}.ifo",
    f"{BASE_NAME}.idx",
    f"{BASE_NAME}.dict",
    "README.txt",
    "SOURCE.json",
    "LICENSE-CC-BY-SA-4.0.txt",
}


def verify_package(path: Path) -> VerificationResult | MDictVerificationResult:
    path = path.resolve()
    if path.is_dir():
        return verify_directory(path)
    if path.is_file() and path.suffix.lower() == ".zip":
        return verify_zip(path)
    if path.is_file() and path.suffix.lower() == ".mdx":
        return verify_mdx(path)
    raise VerificationError(f"{path}: expected a dictionary directory, ZIP, or MDX")


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


def verify_mdx(path: Path) -> MDictVerificationResult:
    path = path.resolve()
    if not path.is_file():
        raise VerificationError(f"{path}: MDX file does not exist")
    data = path.read_bytes()
    cursor = 0

    header_size, cursor = _read_uint(data, cursor, 4, path, "header length")
    header_bytes, cursor = _read_slice(
        data, cursor, header_size, path, "header string"
    )
    header_checksum, cursor = _read_uint(
        data, cursor, 4, path, "header checksum", byteorder="little"
    )
    if header_checksum != _adler32(header_bytes):
        raise VerificationError(f"{path}: invalid MDX header checksum")
    if len(header_bytes) % 2:
        raise VerificationError(f"{path}: MDX header has an odd byte length")
    try:
        header_text = header_bytes.decode("utf-16le")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{path}: MDX header is not UTF-16LE") from exc
    if not header_text.endswith("\r\n\x00"):
        raise VerificationError(f"{path}: invalid MDX header terminator")
    try:
        header = ElementTree.fromstring(header_text[:-1])
    except ElementTree.ParseError as exc:
        raise VerificationError(f"{path}: invalid MDX header XML") from exc
    _verify_mdx_header(header, path)

    key_preamble, cursor = _read_slice(
        data, cursor, 40, path, "keyword section header"
    )
    key_header_checksum, cursor = _read_uint(
        data, cursor, 4, path, "keyword section checksum"
    )
    if key_header_checksum != _adler32(key_preamble):
        raise VerificationError(f"{path}: invalid keyword section checksum")
    (
        key_block_count,
        key_entry_count,
        key_index_raw_size,
        key_index_size,
        key_blocks_size,
    ) = struct.unpack(">QQQQQ", key_preamble)
    if key_block_count < 1 or key_entry_count < 1:
        raise VerificationError(f"{path}: MDX must contain keys")

    key_index_block, cursor = _read_slice(
        data, cursor, key_index_size, path, "keyword block index"
    )
    key_index = _decode_mdx_block(
        key_index_block, key_index_raw_size, path, "keyword block index"
    )
    key_block_info = _parse_key_block_index(
        key_index, key_block_count, key_entry_count, path
    )
    if sum(item[3] for item in key_block_info) != key_blocks_size:
        raise VerificationError(f"{path}: keyword block size total is incorrect")

    keys: list[str] = []
    offsets: list[int] = []
    for block_number, (count, first, last, compressed_size, raw_size) in enumerate(
        key_block_info
    ):
        compressed, cursor = _read_slice(
            data,
            cursor,
            compressed_size,
            path,
            f"keyword block {block_number}",
        )
        raw = _decode_mdx_block(
            compressed, raw_size, path, f"keyword block {block_number}"
        )
        block_keys, block_offsets = _parse_key_block(
            raw, count, path, block_number
        )
        if block_keys[0] != first or block_keys[-1] != last:
            raise VerificationError(
                f"{path}: keyword block {block_number} boundary keys are incorrect"
            )
        keys.extend(block_keys)
        offsets.extend(block_offsets)
    if len(keys) != key_entry_count:
        raise VerificationError(
            f"{path}: keyword count={key_entry_count}, actual={len(keys)}"
        )
    if len(set(keys)) != len(keys) or keys != sorted(keys):
        raise VerificationError(f"{path}: MDX keys are duplicate or incorrectly sorted")
    if not offsets or offsets[0] != 0 or any(
        left >= right for left, right in zip(offsets, offsets[1:])
    ):
        raise VerificationError(f"{path}: MDX record offsets are not strictly ordered")

    record_header, cursor = _read_slice(
        data, cursor, 32, path, "record section header"
    )
    (
        record_block_count,
        record_entry_count,
        record_index_size,
        record_blocks_size,
    ) = struct.unpack(">QQQQ", record_header)
    if record_block_count < 1:
        raise VerificationError(f"{path}: MDX must contain record blocks")
    if record_entry_count != key_entry_count:
        raise VerificationError(f"{path}: keyword and record counts differ")
    if record_index_size != record_block_count * 16:
        raise VerificationError(f"{path}: invalid record block index size")
    record_index, cursor = _read_slice(
        data, cursor, record_index_size, path, "record block index"
    )
    record_block_info = [
        struct.unpack(">QQ", record_index[index : index + 16])
        for index in range(0, len(record_index), 16)
    ]
    if any(compressed < 8 or raw < 1 for compressed, raw in record_block_info):
        raise VerificationError(f"{path}: invalid record block size")
    if sum(item[0] for item in record_block_info) != record_blocks_size:
        raise VerificationError(f"{path}: record block size total is incorrect")

    record_data = bytearray()
    for block_number, (compressed_size, raw_size) in enumerate(record_block_info):
        compressed, cursor = _read_slice(
            data,
            cursor,
            compressed_size,
            path,
            f"record block {block_number}",
        )
        record_data.extend(
            _decode_mdx_block(
                compressed, raw_size, path, f"record block {block_number}"
            )
        )
    if cursor != len(data):
        raise VerificationError(f"{path}: unreferenced trailing MDX bytes")
    if offsets[-1] >= len(record_data):
        raise VerificationError(f"{path}: record offset extends beyond record data")

    for index, (key, start) in enumerate(zip(keys, offsets)):
        end = offsets[index + 1] if index + 1 < len(offsets) else len(record_data)
        record = bytes(record_data[start:end])
        if not record.endswith(b"\x00") or b"\x00" in record[:-1]:
            raise VerificationError(f"{path}: invalid record terminator for {key!r}")
        article_bytes = record[:-1]
        if not article_bytes:
            raise VerificationError(f"{path}: empty article for {key!r}")
        try:
            article = article_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VerificationError(
                f"{path}: article for {key!r} is not valid UTF-8"
            ) from exc
        _verify_html(article, path, start)

    return MDictVerificationResult(
        wordcount=len(keys),
        key_blocks=key_block_count,
        record_blocks=record_block_count,
        mdx_size=len(data),
    )


def _verify_mdx_header(header: ElementTree.Element, path: Path) -> None:
    if header.tag != "Dictionary":
        raise VerificationError(f"{path}: MDX header must be a Dictionary element")
    required = {
        "GeneratedByEngineVersion": MDX_VERSION,
        "RequiredEngineVersion": MDX_VERSION,
        "Encrypted": "0",
        "Encoding": "UTF-8",
        "Format": "Html",
        "Compact": "No",
        "Compat": "No",
        "KeyCaseSensitive": "No",
        "StripKey": "No",
    }
    for name, expected in required.items():
        if header.get(name) != expected:
            raise VerificationError(
                f"{path}: MDX header {name} must be {expected!r}"
            )
    creation_date = header.get("CreationDate")
    try:
        if creation_date is None:
            raise ValueError
        date.fromisoformat(creation_date)
    except ValueError as exc:
        raise VerificationError(f"{path}: invalid MDX creation date") from exc
    if not header.get("Title") or not header.get("Description"):
        raise VerificationError(f"{path}: MDX title and description are required")


def _parse_key_block_index(
    data: bytes, expected_blocks: int, expected_entries: int, path: Path
) -> list[tuple[int, str, str, int, int]]:
    result: list[tuple[int, str, str, int, int]] = []
    cursor = 0
    entry_total = 0
    while cursor < len(data):
        count, cursor = _read_uint(data, cursor, 8, path, "key block entry count")
        first, cursor = _read_index_key(data, cursor, path, "first key")
        last, cursor = _read_index_key(data, cursor, path, "last key")
        compressed_size, cursor = _read_uint(
            data, cursor, 8, path, "key block compressed size"
        )
        raw_size, cursor = _read_uint(
            data, cursor, 8, path, "key block decompressed size"
        )
        if count < 1 or compressed_size < 8 or raw_size < 1:
            raise VerificationError(f"{path}: invalid keyword block index entry")
        result.append((count, first, last, compressed_size, raw_size))
        entry_total += count
    if len(result) != expected_blocks or entry_total != expected_entries:
        raise VerificationError(f"{path}: keyword block index counts are incorrect")
    return result


def _read_index_key(
    data: bytes, cursor: int, path: Path, label: str
) -> tuple[str, int]:
    size, cursor = _read_uint(data, cursor, 2, path, f"{label} length")
    value, cursor = _read_slice(data, cursor, size, path, label)
    terminator, cursor = _read_slice(data, cursor, 1, path, f"{label} terminator")
    if terminator != b"\x00" or not value:
        raise VerificationError(f"{path}: invalid {label}")
    try:
        return value.decode("utf-8"), cursor
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{path}: {label} is not valid UTF-8") from exc


def _parse_key_block(
    data: bytes, expected_count: int, path: Path, block_number: int
) -> tuple[list[str], list[int]]:
    keys: list[str] = []
    offsets: list[int] = []
    cursor = 0
    while cursor < len(data):
        offset, cursor = _read_uint(
            data, cursor, 8, path, f"keyword block {block_number} record offset"
        )
        terminator = data.find(b"\x00", cursor)
        if terminator < 0:
            raise VerificationError(
                f"{path}: unterminated key in keyword block {block_number}"
            )
        value = data[cursor:terminator]
        if not value:
            raise VerificationError(
                f"{path}: empty key in keyword block {block_number}"
            )
        try:
            key = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VerificationError(
                f"{path}: invalid UTF-8 key in keyword block {block_number}"
            ) from exc
        keys.append(key)
        offsets.append(offset)
        cursor = terminator + 1
    if len(keys) != expected_count:
        raise VerificationError(
            f"{path}: keyword block {block_number} count is incorrect"
        )
    return keys, offsets


def _decode_mdx_block(
    block: bytes,
    expected_size: int,
    path: Path,
    label: str,
) -> bytes:
    if len(block) < 8 or block[:4] != b"\x02\x00\x00\x00":
        raise VerificationError(f"{path}: {label} is not zlib-compressed")
    checksum = int.from_bytes(block[4:8], "big")
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(block[8:]) + decompressor.flush()
    except zlib.error as exc:
        raise VerificationError(f"{path}: cannot decompress {label}") from exc
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        raise VerificationError(f"{path}: invalid compressed payload in {label}")
    if len(raw) != expected_size:
        raise VerificationError(f"{path}: decompressed size mismatch in {label}")
    if _adler32(raw) != checksum:
        raise VerificationError(f"{path}: invalid checksum in {label}")
    return raw


def _read_uint(
    data: bytes,
    cursor: int,
    size: int,
    path: Path,
    label: str,
    *,
    byteorder: str = "big",
) -> tuple[int, int]:
    value, cursor = _read_slice(data, cursor, size, path, label)
    return int.from_bytes(value, byteorder), cursor


def _read_slice(
    data: bytes, cursor: int, size: int, path: Path, label: str
) -> tuple[bytes, int]:
    if size < 0 or cursor + size > len(data):
        raise VerificationError(f"{path}: truncated {label}")
    return data[cursor : cursor + size], cursor + size


def _adler32(data: bytes) -> int:
    return zlib.adler32(data) & 0xFFFFFFFF


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
