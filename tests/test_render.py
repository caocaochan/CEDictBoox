from __future__ import annotations

import unittest

from cedict_boox.parser import CedictEntry
from cedict_boox.render import aggregate_entries, render_article


def entry(
    traditional: str,
    simplified: str,
    pinyin: str,
    *senses: str,
    line: int = 1,
) -> CedictEntry:
    return CedictEntry(traditional, simplified, pinyin, senses, line)


class RenderTests(unittest.TestCase):
    def test_aggregates_real_simplified_and_traditional_keys(self) -> None:
        value = entry("中國", "中国", "Zhong1 guo2", "China")
        grouped = aggregate_entries([value])
        self.assertEqual(set(grouped), {"中國", "中国"})
        self.assertIs(grouped["中國"][0], value)

    def test_identical_form_is_only_indexed_once(self) -> None:
        grouped = aggregate_entries([entry("你好", "你好", "ni3 hao3", "hello")])
        self.assertEqual(set(grouped), {"你好"})

    def test_renders_lookup_first_and_escapes_source(self) -> None:
        html = render_article(
            "中国",
            [entry("中國", "中国", "Zhong1 guo2", "China & <Middle>")],
        )
        self.assertEqual(
            html,
            "<div><b>中国</b> <span>〔中國〕</span> <i>Zhōng guó</i></div>"
            "<div>1. China &amp; &lt;Middle&gt;</div>",
        )

    def test_separates_multiple_readings(self) -> None:
        html = render_article(
            "行",
            [
                entry("行", "行", "hang2", "row"),
                entry("行", "行", "xing2", "to walk"),
            ],
        )
        self.assertEqual(html.count("<hr>"), 1)
        self.assertIn("háng", html)
        self.assertIn("xíng", html)


if __name__ == "__main__":
    unittest.main()
