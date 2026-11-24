# coding=utf-8
"""
Short operational alerts to Telegram (fork addition, GPL-3.0).

Used when Bright Data fails or a run crashes, so a broken setup never looks
like "no news today". In dry-run mode alerts are printed instead of sent.
"""

import html
import os
import re
from typing import Dict, List, Optional

import requests

DRY_RUN = False  # set by trendradar.__main__ when --dry-run is active


def run_url() -> str:
    """Link to the current GitHub Actions run, if any."""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if server and repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def _first(value: str) -> str:
    return (value or "").split(";")[0].strip()


def send_alert(config: Dict, title: str, lines: List[str]) -> bool:
    """Send a short alert to the first configured Telegram chat. Returns True if delivered/printed."""
    url = run_url()
    body_lines = [f"⚠️ <b>{html.escape(title)}</b>"] + [html.escape(l) for l in lines if l]
    if url:
        body_lines.append(f'<a href="{html.escape(url)}">Open the run log</a>')
    text = "\n".join(body_lines)[:3900]

    if DRY_RUN:
        print("\n" + "=" * 60 + "\nDRY RUN — alert that would be sent to Telegram:\n" + "-" * 60)
        print(to_plain(text))
        print("=" * 60 + "\n")
        return True

    token = _first(config.get("TELEGRAM_BOT_TOKEN", ""))
    chat_id = _first(config.get("TELEGRAM_CHAT_ID", ""))
    if not token or not chat_id:
        print(f"[alert] Telegram not configured, alert only in log: {title} | {' | '.join(lines)}")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=20,
        )
        ok = resp.status_code == 200 and resp.json().get("ok")
        print(f"[alert] Telegram alert {'sent' if ok else 'FAILED: ' + resp.text[:200]}")
        return bool(ok)
    except Exception as e:
        print(f"[alert] Telegram alert failed: {e}")
        return False


_TAG_RE = re.compile(r"<[^>]+>")


def to_plain(text: str) -> str:
    """Telegram HTML -> readable console text (links shown as 'text (url)')."""
    text = re.sub(r'<a href="([^"]+)">(.*?)</a>', lambda m: f"{m.group(2)} ({html.unescape(m.group(1))})", text)
    return html.unescape(_TAG_RE.sub("", text))


def print_dry_run_batches(channel: str, batches: List[str], report_type: str = "") -> None:
    from trendradar.notification import i18n

    if i18n.ENGLISH:
        report_type = i18n.translate_digest(report_type)
    print("\n" + "=" * 60)
    print(f"DRY RUN — {channel} digest ({len(batches)} message(s)) {report_type}".rstrip())
    for i, b in enumerate(batches, 1):
        print("-" * 60 + f"\n[message {i}/{len(batches)}]\n")
        print(to_plain(b))
    print("=" * 60 + "\n")


def brightdata_failure_lines(result, budget_note: Optional[str] = None) -> List[str]:
    lines = ["No digest was sent this run."]
    for e in result.error_summary()[:6]:
        lines.append(f"• {e}")
    kinds = {e.kind for e in result.errors} | ({result.fatal.kind} if result.fatal else set())
    if "auth" in kinds:
        lines.append("Fix: check the BRIGHTDATA_API_KEY secret and the zone names.")
    if "balance" in kinds:
        lines.append("Fix: top up your Bright Data balance.")
    if "budget" in kinds:
        lines.append("Your own limit in config.yaml → brightdata.limits was reached.")
    if result.balance:
        bal = result.balance.get("balance")
        if bal is not None:
            lines.append(f"Bright Data balance: {bal}")
    if budget_note:
        lines.append(budget_note)
    return lines
