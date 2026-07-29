from __future__ import annotations

import struct
from pathlib import Path
import tempfile
import unittest
from typing import Callable
import zlib

from mdict_utils.base.readmdict import MDX

from cedict_boox.mdict import MDictError, TITLE, write_mdict
from cedict_boox.verify import VerificationError, verify_mdx


ARTICLES = {
    "中国": (
        "<div><b>中国</b> <span>〔中國〕</span> <i>Zhōng guó</i></div>"
        "<div>China</div>"
    ),
    "中國": (
        "<div><b>中國</b> <span>〔中国〕</span> <i>Zhōng guó</i></div>"
        "<div>China</div>"
    ),
    "行": (
        "<div><b>行</b> <i>háng</i></div><div>row · profession</div>"
        "<hr><div><b>行</b> <i>xíng</i></div><div>to walk</div>"
    ),
}


class MDictTests(unittest.TestCase):
    def test_round_trips_with_independent_reader(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dictionary.mdx"
            stats = write_mdict(target, ARTICLES, source_date="2026-07-29")
            verified = verify_mdx(target)
            self.assertEqual(stats.wordcount, len(ARTICLES))
            self.assertEqual(verified.wordcount, len(ARTICLES))
            self.assertEqual(verified.mdx_size, target.stat().st_size)

            reader = MDX(str(target))
            actual = {
                key.decode("utf-8"): value.decode("utf-8")
                for key, value in reader.items()
            }
            self.assertEqual(actual, ARTICLES)
            self.assertIn("中国", actual)
            self.assertIn("中國", actual)
            self.assertIn("<hr>", actual["行"])

    def test_metadata_uses_source_date_and_fixed_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dictionary.mdx"
            write_mdict(target, {"你好": "<div>hello</div>"}, source_date="2026-07-28")
            data = target.read_bytes()
            header_size = int.from_bytes(data[:4], "big")
            header = data[4 : 4 + header_size].decode("utf-16le")
            self.assertIn('CreationDate="2026-07-28"', header)
            self.assertIn(f'Title="{TITLE}"', header)
            self.assertIn('GeneratedByEngineVersion="2.0"', header)
            self.assertIn('Encoding="UTF-8"', header)
            self.assertIn('Format="Html"', header)
            self.assertIn('Encrypted="0"', header)
            self.assertIn('StripKey="No"', header)

    def test_output_is_byte_for_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.mdx"
            second = Path(temp) / "second.mdx"
            write_mdict(first, ARTICLES, source_date="2026-07-29")
            write_mdict(
                second,
                dict(reversed(list(ARTICLES.items()))),
                source_date="2026-07-29",
            )
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_rejects_unrepresentable_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dictionary.mdx"
            with self.assertRaisesRegex(MDictError, "empty dictionary"):
                write_mdict(target, {}, source_date="2026-07-29")
            with self.assertRaisesRegex(MDictError, "headword"):
                write_mdict(
                    target, {"bad\x00key": "value"}, source_date="2026-07-29"
                )
            with self.assertRaisesRegex(MDictError, "NUL"):
                write_mdict(target, {"key": "bad\x00value"}, source_date="2026-07-29")
            with self.assertRaisesRegex(MDictError, "source date"):
                write_mdict(target, {"key": "value"}, source_date="not-a-date")

    def test_verifier_rejects_bad_header_and_block_checksums(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dictionary.mdx"
            write_mdict(target, ARTICLES, source_date="2026-07-29")
            data = bytearray(target.read_bytes())
            header_size = int.from_bytes(data[:4], "big")
            header_checksum = 4 + header_size
            data[header_checksum] ^= 1
            bad_header = Path(temp) / "bad-header.mdx"
            bad_header.write_bytes(data)
            with self.assertRaisesRegex(VerificationError, "header checksum"):
                verify_mdx(bad_header)

            data = bytearray(target.read_bytes())
            key_header = 4 + header_size + 4
            key_index = key_header + 44
            data[key_index + 4] ^= 1
            bad_block = Path(temp) / "bad-block.mdx"
            bad_block.write_bytes(data)
            with self.assertRaisesRegex(VerificationError, "checksum"):
                verify_mdx(bad_block)

    def test_verifier_rejects_bad_offsets_utf8_lengths_and_html(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "dictionary.mdx"
            write_mdict(target, {"a": "<div>x</div>"}, source_date="2026-07-29")

            bad_offset = Path(temp) / "bad-offset.mdx"
            bad_offset.write_bytes(
                _rewrite_only_key_block(
                    target.read_bytes(),
                    lambda raw: b"\x00\x00\x00\x00\x00\x00\x00\x01" + raw[8:],
                )
            )
            with self.assertRaisesRegex(VerificationError, "offset"):
                verify_mdx(bad_offset)

            bad_key_utf8 = Path(temp) / "bad-key-utf8.mdx"
            bad_key_utf8.write_bytes(
                _rewrite_only_key_block(
                    target.read_bytes(),
                    lambda raw: raw[:8] + b"\xff" + raw[9:],
                )
            )
            with self.assertRaisesRegex(VerificationError, "UTF-8 key"):
                verify_mdx(bad_key_utf8)

            bad_utf8 = Path(temp) / "bad-utf8.mdx"
            bad_utf8.write_bytes(
                _rewrite_only_record_block(
                    target.read_bytes(), lambda raw: raw.replace(b"x", b"\xff", 1)
                )
            )
            with self.assertRaisesRegex(VerificationError, "UTF-8"):
                verify_mdx(bad_utf8)

            bad_length_data = bytearray(target.read_bytes())
            record_header = _record_header_offset(bad_length_data)
            declared = int.from_bytes(
                bad_length_data[record_header + 24 : record_header + 32], "big"
            )
            bad_length_data[record_header + 24 : record_header + 32] = (
                declared + 1
            ).to_bytes(8, "big")
            bad_length = Path(temp) / "bad-length.mdx"
            bad_length.write_bytes(bad_length_data)
            with self.assertRaisesRegex(VerificationError, "size total"):
                verify_mdx(bad_length)

            bad_html = Path(temp) / "bad-html.mdx"
            write_mdict(
                bad_html, {"a": "<script>x</script>"}, source_date="2026-07-29"
            )
            with self.assertRaisesRegex(VerificationError, "disallowed HTML"):
                verify_mdx(bad_html)

            unordered_source = Path(temp) / "unordered-source.mdx"
            write_mdict(
                unordered_source,
                {"a": "<div>a</div>", "b": "<div>b</div>"},
                source_date="2026-07-29",
            )
            unordered = Path(temp) / "unordered.mdx"
            unordered.write_bytes(
                _rewrite_only_key_block(
                    unordered_source.read_bytes(),
                    _swap_two_single_byte_keys,
                    _swap_index_boundary_keys,
                )
            )
            with self.assertRaisesRegex(VerificationError, "incorrectly sorted"):
                verify_mdx(unordered)


def _block(raw: bytes) -> bytes:
    return (
        b"\x02\x00\x00\x00"
        + (zlib.adler32(raw) & 0xFFFFFFFF).to_bytes(4, "big")
        + zlib.compress(raw, level=9)
    )


def _header_end(data: bytes | bytearray) -> int:
    return 4 + int.from_bytes(data[:4], "big") + 4


def _record_header_offset(data: bytes | bytearray) -> int:
    key_header = _header_end(data)
    _, _, _, key_index_size, key_blocks_size = struct.unpack(
        ">QQQQQ", data[key_header : key_header + 40]
    )
    return key_header + 44 + key_index_size + key_blocks_size


def _rewrite_only_key_block(
    data: bytes,
    transform: Callable[[bytes], bytes],
    index_transform: Callable[[bytes], bytes] | None = None,
) -> bytes:
    key_header = _header_end(data)
    preamble = struct.unpack(">QQQQQ", data[key_header : key_header + 40])
    block_count, entry_count, _, index_size, blocks_size = preamble
    if block_count != 1:
        raise AssertionError("test helper requires one key block")
    index_start = key_header + 44
    index_block = data[index_start : index_start + index_size]
    index_raw = zlib.decompress(index_block[8:])
    block_start = index_start + index_size
    old_block = data[block_start : block_start + blocks_size]
    raw = zlib.decompress(old_block[8:])
    new_raw = transform(raw)
    new_block = _block(new_raw)
    boundary_index = index_transform(index_raw) if index_transform else index_raw
    new_index_raw = boundary_index[:-16] + struct.pack(
        ">QQ", len(new_block), len(new_raw)
    )
    new_index = _block(new_index_raw)
    new_preamble = struct.pack(
        ">QQQQQ",
        block_count,
        entry_count,
        len(new_index_raw),
        len(new_index),
        len(new_block),
    )
    return (
        data[:key_header]
        + new_preamble
        + (zlib.adler32(new_preamble) & 0xFFFFFFFF).to_bytes(4, "big")
        + new_index
        + new_block
        + data[block_start + blocks_size :]
    )


def _rewrite_only_record_block(
    data: bytes, transform: Callable[[bytes], bytes]
) -> bytes:
    record_header = _record_header_offset(data)
    block_count, entry_count, index_size, blocks_size = struct.unpack(
        ">QQQQ", data[record_header : record_header + 32]
    )
    if block_count != 1 or index_size != 16:
        raise AssertionError("test helper requires one record block")
    block_start = record_header + 32 + index_size
    old_block = data[block_start : block_start + blocks_size]
    raw = zlib.decompress(old_block[8:])
    new_raw = transform(raw)
    new_block = _block(new_raw)
    new_header = struct.pack(">QQQQ", block_count, entry_count, 16, len(new_block))
    new_index = struct.pack(">QQ", len(new_block), len(new_raw))
    return data[:record_header] + new_header + new_index + new_block


def _swap_two_single_byte_keys(raw: bytes) -> bytes:
    value = bytearray(raw)
    value[8], value[18] = value[18], value[8]
    return bytes(value)


def _swap_index_boundary_keys(raw: bytes) -> bytes:
    value = bytearray(raw)
    value[10], value[14] = value[14], value[10]
    return bytes(value)


if __name__ == "__main__":
    unittest.main()
