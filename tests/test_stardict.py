from __future__ import annotations

from pathlib import Path
import hashlib
import struct
import tempfile
import unittest

from cedict_boox.stardict import (
    BASE_NAME,
    sorted_headwords,
    stardict_compare,
    write_stardict,
)


class StarDictTests(unittest.TestCase):
    def test_comparison_matches_ascii_case_then_binary(self) -> None:
        self.assertLess(stardict_compare("A", "a"), 0)
        self.assertLess(stardict_compare("a", "B"), 0)
        self.assertLess(stardict_compare("中国", "中國"), 0)
        self.assertEqual(
            sorted_headwords(["b", "A", "a", "中國", "中国"]),
            ["A", "a", "b", "中国", "中國"],
        )

    def test_writer_uses_big_endian_offsets_and_exact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            stats = write_stardict(
                output,
                {"中國": "<div>traditional</div>", "中国": "<div>simple</div>"},
                source_date="2026-07-28",
            )
            idx = (output / f"{BASE_NAME}.idx").read_bytes()
            terminator = idx.index(0)
            offset, size = struct.unpack("!II", idx[terminator + 1 : terminator + 9])
            self.assertEqual(offset, 0)
            self.assertEqual(size, len("<div>simple</div>".encode()))
            ifo = (output / f"{BASE_NAME}.ifo").read_text(encoding="utf-8")
            self.assertIn("version=2.4.2\n", ifo)
            self.assertIn(f"wordcount={stats.wordcount}\n", ifo)
            self.assertIn(f"idxfilesize={stats.idx_size}\n", ifo)
            self.assertNotIn("idxoffsetbits", ifo)
            combined = b"".join(
                (output / f"{BASE_NAME}.{extension}").read_bytes()
                for extension in ("ifo", "idx", "dict")
            )
            self.assertEqual(
                hashlib.sha256(combined).hexdigest(),
                "2d3038838ad10124f84a5f19653bb5ee996696f9ec2bae34889dd57a8f7801f2",
            )


if __name__ == "__main__":
    unittest.main()
