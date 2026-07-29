from __future__ import annotations

import unittest

from cedict_boox.pinyin import PinyinError, numeric_to_tone_marks


class PinyinTests(unittest.TestCase):
    def test_required_examples(self) -> None:
        cases = {
            "Zhong1 guo2": "Zhōng guó",
            "nu:3 ren2": "nǚ rén",
            "shui3": "shuǐ",
            "lu:e4": "lüè",
            "hua1 r5": "huār",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(numeric_to_tone_marks(source), expected)

    def test_vowel_rules_neutral_and_punctuation(self) -> None:
        self.assertEqual(
            numeric_to_tone_marks("liu2 gui1 ou3 ma5 , hao3"),
            "liú guī ǒu ma , hǎo",
        )

    def test_real_er_is_separate(self) -> None:
        self.assertEqual(numeric_to_tone_marks("nu:3 er2"), "nǚ ér")

    def test_borrowed_latin_tokens_are_preserved(self) -> None:
        self.assertEqual(numeric_to_tone_marks("san1 D da3 yin4"), "sān D dǎ yìn")
        self.assertEqual(numeric_to_tone_marks("ky"), "ky")
        self.assertEqual(numeric_to_tone_marks("11 Qu1"), "11 Qū")

    def test_legacy_concatenated_syllables(self) -> None:
        self.assertEqual(numeric_to_tone_marks("shi2ke4"), "shíkè")
        self.assertEqual(numeric_to_tone_marks("Mao2 : Ze2"), "Máo : Zé")

    def test_syllabic_consonant(self) -> None:
        self.assertEqual(numeric_to_tone_marks("m2"), "ḿ")

    def test_rejects_bad_tone(self) -> None:
        with self.assertRaises(PinyinError):
            numeric_to_tone_marks("ma6")


if __name__ == "__main__":
    unittest.main()
