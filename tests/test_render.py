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

    def test_renders_inline_definitions_and_escapes_source(self) -> None:
        html = render_article(
            "中国",
            [
                entry(
                    "中國",
                    "中国",
                    "Zhong1 guo2",
                    "China & <Middle>",
                    '"Middle Kingdom"',
                )
            ],
        )
        self.assertEqual(
            html,
            "<div><b>中国</b> <span>〔中國〕</span> <i>Zhōng guó</i></div>"
            "<div>China &amp; &lt;Middle&gt; · &quot;Middle Kingdom&quot;</div>",
        )

    def test_single_definition_has_no_separator(self) -> None:
        html = render_article(
            "你好",
            [entry("你好", "你好", "ni3 hao3", "hello")],
        )
        self.assertEqual(
            html,
            "<div><b>你好</b> <i>nǐ hǎo</i></div><div>hello</div>",
        )
        self.assertNotIn(" · ", html)

    def test_separates_multiple_readings(self) -> None:
        html = render_article(
            "行",
            [
                entry("行", "行", "hang2", "row", "profession"),
                entry("行", "行", "xing2", "to walk"),
            ],
        )
        self.assertEqual(
            html,
            "<div><b>行</b> <i>háng</i></div><div>row · profession</div>"
            "<hr>"
            "<div><b>行</b> <i>xíng</i></div><div>to walk</div>",
        )


if __name__ == "__main__":
    unittest.main()
