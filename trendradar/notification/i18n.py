# coding=utf-8
"""
English labels for notification digests (fork addition, GPL-3.0).

Upstream TrendRadar writes its digest labels in Chinese inside the message
builders. Instead of rewriting those builders, the finished message text is
translated here, label by label. News titles are never touched: only the
fixed label strings below (all Chinese) are replaced, and news titles from
Bright Data sources are in the language of the source.

Enabled with config.yaml → app.digest_language: "en" (the fork default).
"""

import re
from typing import List, Tuple

# Longest / most specific first.
_PHRASES: List[Tuple[str, str]] = [
    # labels directly followed by a colon (often closed by </b> right after)
    ("总新闻：", "Total:"),
    ("最热话题：", "Top topics:"),
    ("报告类型：", "Report type:"),
    ("联网搜索：", "Web search:"),
    ("独立展示：", "Full lists:"),
    ("AI 分析：", "AI analysis:"),
    ("增量模式下暂无新增匹配的热点词汇", "No new items match your topics"),
    ("当前榜单模式下暂无匹配的热点词汇", "No items match your topics right now"),
    ("暂无匹配的热点词汇", "No items match your topics"),
    ("数据获取失败的平台", "Sources that failed"),
    ("本次新增热点新闻", "New this run"),
    ("联网搜索本次新增", "New from web search"),
    ("TrendRadar 发现新版本", "New TrendRadar version available"),
    ("TrendRadar 热点分析报告", "TrendRadar report"),
    ("热点词汇统计", "Topics"),
    ("热点新闻统计", "By source"),
    ("热点分析报告", "Report"),
    ("联网搜索结果", "Web search results"),
    ("RSS 订阅统计", "RSS feeds"),
    ("RSS 本次新增", "New in RSS"),
    ("独立展示区", "Full lists"),
    ("最热话题", "Top topics"),
    ("全天汇总", "Daily summary"),
    ("当前榜单", "Current"),
    ("增量分析", "New items"),
    ("更新时间", "Updated"),
    ("生成时间", "Generated"),
    ("报告类型", "Report type"),
    ("总新闻", "Total"),
    ("联网搜索", "Web search"),
    ("独立展示", "Full lists"),
    ("AI 分析", "AI analysis"),
    ("未知来源", "Unknown source"),
    ("未命名主题", "Untitled topic"),
    ("无标题", "Untitled"),
    ("&#91;权威&#93;", "&#91;trusted&#93;"),
    ("[权威]", "[trusted]"),
    # short words only in the exact label contexts they appear in
    ("（新增 热榜 ", " (new: collected "),
    (" + 搜索 ", " + search "),
    ("热榜：", "Collected:"),
    ("类型：", "Type:"),
    ("时间：", "Time:"),
    ("（平台 ", " (sources "),
    ("（主题 ", " (topics "),
    ("（新增 ", " (new "),
    ("（源 ", " (feeds "),
    ("**，当前 **", "**, now **"),
    ("*，当前 *", "*, now *"),
    ("，当前 ", ", now "),
]

ENGLISH = False  # set at startup from app.digest_language

_REGEX: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"第\s*(\d+)\s*/\s*(\d+)\s*批次"), r"Part \1/\2"),
    (re.compile(r"\(\s*共\s*([0-9,]+)\s*条\s*\)"), r"(\1 items)"),
    (re.compile(r"共\s*([0-9,]+)\s*条"), r"\1 items"),
    (re.compile(r"\((\d+)次\)"), r"(×\1)"),
    (re.compile(r"(\d+)\s*条"), r"\1 items"),
    (re.compile(r"(\d+)月(\d+)日"), r"\1/\2"),
]

_PUNCT = [("（", " ("), ("）", ")"), ("：", ": "), ("，", ", "), ("、", ", ")]


def translate_digest(text: str) -> str:
    """Replace the fixed Chinese labels of a digest message with English ones.

    Only the full-width punctuation that upstream uses around labels is converted;
    titles in Chinese would keep their characters but get ASCII punctuation.
    """
    if not text:
        return text
    for rx, rep in _REGEX:
        text = rx.sub(rep, text)
    for zh, en in _PHRASES:
        text = text.replace(zh, en)
    for zh, en in _PUNCT:
        text = text.replace(zh, en)
    # tidy spaces created by punctuation replacement: "Total:  4" -> "Total: 4", "( " -> "("
    text = re.sub(r"(?<=\S)[ \t]{2,}", " ", text)   # keep indentation at line start
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = text.replace("( ", "(").replace(" :", ":")
    text = re.sub(r"(?<![\d.,])\b1 items\b", "1 item", text)
    return text


def is_english(config) -> bool:
    return str((config or {}).get("DIGEST_LANGUAGE", "zh")).lower().startswith("en")
