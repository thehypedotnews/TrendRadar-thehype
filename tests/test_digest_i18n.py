# coding=utf-8
"""English digest labels (fork addition)."""

import unittest

from trendradar.notification.i18n import translate_digest


class DigestI18nTests(unittest.TestCase):
    def test_labels(self):
        zh = (
            "<b>[第 1/2 批次]</b>\n\n"
            "<b>总新闻：</b> 12 条（新增 热榜 3 + 搜索 1）\n"
            "<b>热榜：</b> 10/40（平台 2/3）\n"
            "<b>类型：</b> 当前榜单\n"
            "📊 <b>热点词汇统计</b> (共 10 条)\n"
            "📌 [1/2] AI : 1 条\n"
            "  1. [Reddit] Some title (3次)\n"
            "🆕 <b>本次新增热点新闻</b> (共 2 条)\n"
            "⚠️ <b>数据获取失败的平台：</b>\n"
            "更新时间：2026-11-24 10:00"
        )
        en = translate_digest(zh)
        self.assertIn("[Part 1/2]", en)
        self.assertIn("Total:</b> 12 items (new: collected 3 + search 1)", en)
        self.assertIn("Collected:</b> 10/40 (sources 2/3)", en)
        self.assertIn("Type:</b> Current", en)
        self.assertIn("Topics</b> (10 items)", en)
        self.assertIn("AI: 1 item\n", en)
        self.assertIn("  1. [Reddit] Some title (×3)", en)
        self.assertIn("New this run</b> (2 items)", en)
        self.assertIn("Sources that failed:", en)
        self.assertIn("Updated: 2026-11-24 10:00", en)
        self.assertNotRegex(en, r"[一-鿿]")

    def test_titles_untouched(self):
        self.assertEqual(translate_digest("  2. [X] OpenAI ships 10 items today"), "  2. [X] OpenAI ships 10 items today")


if __name__ == "__main__":
    unittest.main()
