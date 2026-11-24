# coding=utf-8
"""
Ranking for items collected through Bright Data (fork addition, GPL-3.0).

The original TrendRadar ranks by hot-list position. Bright Data sources have
no hot list, so items are ranked by

    score = 100 * (w_f * F + w_e * E + w_r * R)        default weights 0.45 / 0.35 / 0.20

    F  freshness   = 2 ** (-age_hours / half_life)     half_life = 12 h
                     no published time -> unknown_time_factor * that, age taken from first_seen
    E  engagement  = 0..1, computed when the item is collected:
                       social: percentile of log1p(raw engagement) within the same source and run
                       search / listing pages: (N - position + 1) / N
                       single web page: web_default_engagement (0.3)
    R  recurrence  = 0.6 * min(1, (runs_seen - 1) / runs_cap)
                   + 0.4 * min(1, (sources - 1) / (sources_cap - 1))

The registry below is filled once per run by the Bright Data collector and read
by core.analyzer.calculate_news_weight when weight.mode is "brightdata".
"""

import math
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ItemSignals:
    published_ts: Optional[float]
    first_seen_ts: float
    engagement_pct: float
    runs_seen: int
    n_sources: int


@dataclass
class RankingConfig:
    freshness_weight: float = 0.45
    engagement_weight: float = 0.35
    recurrence_weight: float = 0.20
    half_life_hours: float = 12.0
    unknown_time_factor: float = 0.5
    runs_cap: int = 6
    sources_cap: int = 3

    @classmethod
    def from_dict(cls, d: Optional[Dict]) -> "RankingConfig":
        d = d or {}
        c = cls()
        for k in c.__dataclass_fields__:
            if k in d and d[k] is not None:
                setattr(c, k, type(getattr(c, k))(d[k]))
        return c


def freshness(sig: ItemSignals, now_ts: float, cfg: RankingConfig) -> float:
    if sig.published_ts:
        age_h = max(0.0, (now_ts - sig.published_ts) / 3600.0)
        factor = 1.0
    else:
        age_h = max(0.0, (now_ts - sig.first_seen_ts) / 3600.0)
        factor = cfg.unknown_time_factor
    return factor * 2 ** (-age_h / max(cfg.half_life_hours, 0.1))


def recurrence(sig: ItemSignals, cfg: RankingConfig) -> float:
    runs_part = min(1.0, max(0, sig.runs_seen - 1) / max(cfg.runs_cap, 1))
    src_part = min(1.0, max(0, sig.n_sources - 1) / max(cfg.sources_cap - 1, 1))
    return 0.6 * runs_part + 0.4 * src_part


def score(sig: ItemSignals, now_ts: Optional[float] = None, cfg: Optional[RankingConfig] = None) -> float:
    cfg = cfg or RankingConfig()
    now_ts = now_ts if now_ts is not None else time.time()
    e = min(1.0, max(0.0, sig.engagement_pct))
    return 100.0 * (
        cfg.freshness_weight * freshness(sig, now_ts, cfg)
        + cfg.engagement_weight * e
        + cfg.recurrence_weight * recurrence(sig, cfg)
    )


def percentile_ranks(values):
    """Mid-rank percentiles of log1p(values) in 0..1; ties share a rank. One value -> 0.5."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    logs = [math.log1p(max(0.0, v)) for v in values]
    order = sorted(range(n), key=lambda i: logs[i])
    out = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and logs[order[j + 1]] == logs[order[i]]:
            j += 1
        mid = (i + j) / 2.0
        for k in range(i, j + 1):
            out[order[k]] = mid / (n - 1)
        i = j + 1
    return out


# --------------------------------------------------------------- registry

_SIGNALS: Dict[str, ItemSignals] = {}
_CONFIG = RankingConfig()
_NOW: Optional[float] = None


def set_signals(signals: Dict[str, ItemSignals], cfg: Optional[RankingConfig] = None, now_ts: Optional[float] = None) -> None:
    global _SIGNALS, _CONFIG, _NOW
    _SIGNALS = dict(signals)
    if cfg is not None:
        _CONFIG = cfg
    _NOW = now_ts


def clear_signals() -> None:
    set_signals({})


def lookup_score(url: str) -> Optional[float]:
    """Score for a collected URL, or None if the URL did not come from Bright Data."""
    if not url or not _SIGNALS:
        return None
    from trendradar.crawler.brightdata.models import url_key

    sig = _SIGNALS.get(url_key(url))
    if sig is None:
        return None
    return score(sig, _NOW, _CONFIG)
