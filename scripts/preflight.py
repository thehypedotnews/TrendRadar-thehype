#!/usr/bin/env python3
# coding=utf-8
"""
Preflight check: are all required secrets set for the features enabled in config.yaml?

Runs as the first step of the "Get Hot News" workflow and fails fast with a
list of missing secret NAMES (values are never printed). Also usable locally:

    uv run python scripts/preflight.py

Part of the thehypedotnews/TrendRadar-thehype fork of TrendRadar (GPL-3.0).
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:  # pragma: no cover
    print("preflight: PyYAML is required (uv run --with pyyaml python scripts/preflight.py)")
    sys.exit(2)

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config/config.yaml")
ON_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"


def load_dotenv(path=".env"):
    if ON_ACTIONS or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            v = v.strip().strip("'\"")
            if v and k.strip() not in os.environ:
                os.environ[k.strip()] = v


def is_set(name):
    return bool(os.environ.get(name, "").strip())


def env_bool(name):
    v = os.environ.get(name, "").strip().lower()
    return None if not v else v in ("1", "true", "yes", "on")


def check_telegram_token(token):
    """Ask Telegram whether the token is valid (getMe). Returns a list of problems."""
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        if data.get("ok"):
            print(f"Telegram bot token OK (bot: @{data.get('result', {}).get('username', '?')})")
            return []
        return ["Telegram did not accept TELEGRAM_BOT_TOKEN"]
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            return [f"Telegram does not know this TELEGRAM_BOT_TOKEN (HTTP {e.code}). Copy the token again from "
                    "@BotFather (/mybots → your bot → API Token) and update the secret."]
        print(f"Note: could not verify the Telegram token (HTTP {e.code}); continuing")
        return []
    except Exception as e:  # network problems should not block the run
        print(f"Note: could not reach Telegram to verify the token ({e.__class__.__name__}); continuing")
        return []


def main():
    load_dotenv()
    if not os.path.exists(CONFIG_PATH):
        print(f"::error::{CONFIG_PATH} not found")
        return 1
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    required = []   # (name, why)
    problems = []   # config problems (not secrets)

    bd = cfg.get("brightdata", {}) or {}
    bd_enabled = env_bool("BRIGHTDATA_ENABLED")
    bd_enabled = bd.get("enabled", False) if bd_enabled is None else bd_enabled
    if bd_enabled:
        required.append(("BRIGHTDATA_API_KEY", "Bright Data is enabled"))
        serp = bd.get("serp", {}) or {}
        if serp.get("enabled") and serp.get("queries") and not serp.get("zone"):
            required.append(("BRIGHTDATA_SERP_ZONE", "brightdata.serp is enabled"))
        web = bd.get("web", {}) or {}
        if web.get("enabled") and web.get("pages") and not web.get("zone"):
            required.append(("BRIGHTDATA_UNLOCKER_ZONE", "brightdata.web is enabled"))
        sources_on = [s for s in ("serp", "reddit", "x", "youtube", "web") if (bd.get(s) or {}).get("enabled")]
        if not sources_on:
            problems.append("brightdata.enabled is true but no source under it is enabled")

    notification = cfg.get("notification", {}) or {}
    if notification.get("enabled", True) and env_bool("DRY_RUN") is not True:
        required.append(("TELEGRAM_BOT_TOKEN", "digests are sent to Telegram"))
        required.append(("TELEGRAM_CHAT_ID", "digests are sent to Telegram"))

    s3_names = ("S3_BUCKET_NAME", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL")
    s3_set = [n for n in s3_names if is_set(n)]
    if s3_set and len(s3_set) < len(s3_names):
        # half-configured R2 is almost certainly a mistake
        for name in s3_names:
            required.append((name, "some S3_* secrets are set, so all four are needed for R2/S3 storage"))
    elif ON_ACTIONS and not s3_set:
        print("Note: no R2/S3 secrets — history between runs is kept in the GitHub Actions cache.")

    ai_users = []
    ai_analysis = env_bool("AI_ANALYSIS_ENABLED")
    if (cfg.get("ai_analysis", {}) or {}).get("enabled") if ai_analysis is None else ai_analysis:
        ai_users.append("ai_analysis.enabled")
    if (cfg.get("ai_translation", {}) or {}).get("enabled"):
        ai_users.append("ai_translation.enabled")
    if (cfg.get("filter", {}) or {}).get("method") == "ai":
        ai_users.append("filter.method: ai")
    if ai_users and not (cfg.get("ai", {}) or {}).get("api_key"):
        required.append(("AI_API_KEY", "enabled in config: " + ", ".join(ai_users)))

    # Telegram: catch the most common copy/paste mistakes before anything runs (values are never printed)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token:
        if token.lower().startswith("bot"):
            problems.append("TELEGRAM_BOT_TOKEN starts with 'bot' — paste only the token itself, like 123456789:AAH...")
        elif not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token):
            problems.append("TELEGRAM_BOT_TOKEN does not look like a bot token (expected 123456789:AAH... from @BotFather; "
                            "no quotes, spaces or extra text)")
        else:
            problems.extend(check_telegram_token(token))
    if chat and not re.fullmatch(r"-?\d{5,}|@[A-Za-z0-9_]{5,}", chat):
        problems.append("TELEGRAM_CHAT_ID should be a number like 123456789 (private chat) or -1001234567890 "
                        "(channel/group), or @channelname")

    missing = [(n, why) for n, why in required if not is_set(n)]
    seen = set()
    print("Preflight check")
    print("-" * 60)
    for name, why in required:
        if name in seen:
            continue
        seen.add(name)
        print(f"  {'OK     ' if is_set(name) else 'MISSING'}  {name}")
    print("-" * 60)

    for p in problems:
        print(f"::error::{p}")
        print(f"PROBLEM: {p}")
    if missing:
        names = []
        for name, why in missing:
            if name not in names:
                names.append(name)
                print(f"::error title=Missing secret {name}::{name} is not set ({why}).")
        print()
        print("Missing secrets: " + ", ".join(names))
        if ON_ACTIONS:
            print("Add them in GitHub: Settings → Secrets and variables → Actions → New repository secret,")
            print("or with the GitHub CLI:  gh secret set NAME   (it asks for the value)")
        else:
            print("Add them to your .env file (see .env.example).")
        return 1
    if problems:
        return 1
    print("All required secrets are set.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
