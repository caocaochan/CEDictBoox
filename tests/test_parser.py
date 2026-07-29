from __future__ import annotations

import unittest

from cedict_boox.parser import CedictParseError, parse_cedict_text


class ParserTests(unittest.TestCase):
    def test_parses_senses_and_metadata(self) -> None:
        document = parse_cedict_text(
            "#! version=1\n#! entries=1\n中國 中国 [Zhong1 guo2] /China/Middle Kingdom/\n"
        )
        self.assertEqual(document.metadata["version"], "1")
        self.assertEqual(document.entries[0].senses, ("China", "Middle Kingdom"))

    def test_rejects_malformed_data_line(self) -> None:
        with self.assertRaisesRegex(CedictParseError, "invalid CC-CEDICT V1"):
            parse_cedict_text("中國 中国 [[Zhong1 guo2]] /China/\n", "fixture")

    def test_rejects_duplicate_identity(self) -> None:
        value = (
            "行 行 [xing2] /to walk/\n"
            "行 行 [xing2] /to be OK/\n"
        )
        with self.assertRaisesRegex(CedictParseError, "duplicate"):
            parse_cedict_text(value)

    def test_rejects_declared_count_mismatch(self) -> None:
        with self.assertRaisesRegex(CedictParseError, "header declares 2"):
            parse_cedict_text("#! entries=2\n你 你 [ni3] /you/\n")

    def test_accepts_ascii_numeric_headword(self) -> None:
        entry = parse_cedict_text(
            "3D打印 3D打印 [san1 D da3 yin4] /3D printing/\n"
        ).entries[0]
        self.assertEqual(entry.simplified, "3D打印")


if __name__ == "__main__":
    unittest.main()

