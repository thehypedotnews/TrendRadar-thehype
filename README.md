<div align="center">

# 📡 TrendRadar · Bright Data edition

**Your own news radar: fresh items from Google News, Reddit, X, YouTube and any web page,<br>filtered by your topics and delivered to Telegram a few times a day. Runs free on GitHub Actions.**

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg?style=flat-square)](LICENSE)
[![Fork of TrendRadar](https://img.shields.io/badge/fork%20of-sansan0%2FTrendRadar-24292f?style=flat-square&logo=github)](https://github.com/sansan0/TrendRadar)
[![Data: Bright Data](https://img.shields.io/badge/data-Bright%20Data-3d7ffc?style=flat-square)](https://brightdata.com)
[![Runs on GitHub Actions](https://img.shields.io/badge/runs%20on-GitHub%20Actions-2088FF?style=flat-square&logo=githubactions&logoColor=white)](#-setup)
[![Delivers to Telegram](https://img.shields.io/badge/delivers%20to-Telegram-26A5E4?style=flat-square&logo=telegram&logoColor=white)](#-setup)

</div>

> [!NOTE]
> **Fork of [TrendRadar](https://github.com/sansan0/TrendRadar) by [sansan0](https://github.com/sansan0)**, modified by thehypedotnews in November 2026.
> GPL-3.0 like the original. All upstream copyright notices are kept.
> Original docs: [README-EN.md](README-EN.md) (English) · [README-ZH.md](README-ZH.md) (中文).

---

## ✨ What's different from the original

| | Original TrendRadar | This fork |
|---|---|---|
| **Data** | newsnow hot lists (Chinese platforms), RSS | **Bright Data**: Google News / Google, Reddit, X, YouTube, any web page. newsnow and RSS are still in the code, off in config. |
| **Ranking** | position on hot lists | **freshness + engagement + recurrence** ([how](#-how-ranking-works)) |
| **Memory** | per day | items already sent on an earlier day are not sent again |
| **Failures** | logged | Telegram alert + red run; **preflight** names missing secrets up front |
| **Testing** | — | **dry run**: prints the digest instead of sending it |
| **Defaults** | Chinese, Asia/Shanghai, hourly | English digest, neutral topics, UTC, 3 runs a day, AI off |

---

## 🚀 Setup

About 30 minutes. You need **GitHub**, **Telegram** and **Bright Data**. **Cloudflare R2** is optional.

<details open>
<summary><b>1. Copy this repository</b></summary>

**Use this template → Create a new repository**. Use the template button, not *Fork*: in forks, Actions are off by default and scheduled runs misbehave ([upstream #606](https://github.com/sansan0/TrendRadar/issues/606)).

**Public** repositories get unlimited free Actions minutes. **Private** ones are fine too, but runs can wait up to 30 min for scrapers (see [Cost](#-cost)).

> 📷 *Screenshot placeholder: "Use this template → Create a new repository"*
</details>

<details open>
<summary><b>2. Telegram bot</b></summary>

1. In **[@BotFather](https://t.me/BotFather)** send `/newbot` and copy the token (`123456789:AAH…`). This is **`TELEGRAM_BOT_TOKEN`**.
2. Open your bot, press **Start** and send it any message. Without this, the bot is not allowed to write to you.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`. This is **`TELEGRAM_CHAT_ID`**.
   - For a private chat, your own account ID works too.
   - **Channel or group instead:** add the bot as an admin, post a message there, and use the ID that starts with `-100`.

> 📷 *Screenshot placeholder: BotFather token · getUpdates chat id*
</details>

<details open>
<summary><b>3. Bright Data</b></summary>

1. Sign up at **[brightdata.com](https://brightdata.com)**. There is a monthly free tier.
2. **Account settings → Users and API keys** → create a key. This is **`BRIGHTDATA_API_KEY`**.
3. Open **SERP API** and create a zone. Its name is **`BRIGHTDATA_SERP_ZONE`**.
4. *Only if you enable `brightdata.web`:* create a **Web Unlocker** zone. Its name is **`BRIGHTDATA_UNLOCKER_ZONE`**.
5. Reddit, X and YouTube use the **Web Scraper API**, which needs no zone. Check that you have a balance or free credits.

> 📷 *Screenshot placeholder: API key page · SERP API zone name*
</details>

<details>
<summary><b>4. (Optional) Cloudflare R2 for permanent history</b></summary>

GitHub Actions forgets its files after every run, so the history has to be kept somewhere else. It is used for dedup, the daily request limit and recurrence in the ranking.

- **Without R2 (default):** the history is kept in the **GitHub Actions cache**. Nothing to set up. GitHub may drop the cache after 7 days without runs; after that, a few items can repeat once.
- **With R2:** the history is kept permanently in your own bucket. Cloudflare may ask for a card to activate R2, but the free tier is far more than this needs.
  1. **R2 → Create bucket** → the bucket name is `S3_BUCKET_NAME`.
  2. **R2 → API Tokens → Create token** with **Object Read & Write** on that bucket → `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY`.
  3. Set `S3_ENDPOINT_URL` = `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` and `S3_REGION` = `auto`.

> 📷 *Screenshot placeholder: R2 API token form*
</details>

<details open>
<summary><b>5. Add secrets</b></summary>

**Settings → Secrets and variables → Actions → New repository secret.** Names must match exactly.

| Secret | Needed |
|---|---|
| `BRIGHTDATA_API_KEY` | ✅ always |
| `BRIGHTDATA_SERP_ZONE` | ✅ with `brightdata.serp` (on by default) |
| `BRIGHTDATA_UNLOCKER_ZONE` | only with `brightdata.web` |
| `TELEGRAM_BOT_TOKEN` · `TELEGRAM_CHAT_ID` | ✅ always |
| `S3_BUCKET_NAME` · `S3_ACCESS_KEY_ID` · `S3_SECRET_ACCESS_KEY` · `S3_ENDPOINT_URL` · `S3_REGION` | only with R2 (all or none) |
| `AI_API_KEY` · `AI_MODEL` | only if you turn on AI features |

With the GitHub CLI, run `gh secret set BRIGHTDATA_API_KEY` (and so on for each name). It asks for the value, so it never lands in your shell history.

> 📷 *Screenshot placeholder: New repository secret form*
</details>

<details open>
<summary><b>6. Pick topics → enable Actions → first run</b></summary>

1. **Topics.** Edit them on GitHub with the ✏️ button. See [Choosing topics](#-choosing-topics).
2. **Enable Actions.** In the **Actions** tab, click *"I understand my workflows…"* if it appears. Then open **Get Hot News** and click **Enable workflow** if it is disabled.
3. **First run.** **Get Hot News → Run workflow** with **Dry run** ticked. The log of the **Run crawler** step shows the digest it *would* send. If **Preflight** fails, it names exactly what is missing.
4. **Real run.** Run it again without Dry run, and the digest arrives in Telegram.
5. **Schedule.** After that it runs by itself at **06:17 · 12:17 · 18:17 UTC**. To change this, edit `cron:` in `.github/workflows/crawler.yml` (times are always UTC).

> 📷 *Screenshot placeholder: Run workflow with Dry run · digest in Telegram*
</details>

<details open>
<summary><b>7. Check In every 7 days</b></summary>

Scheduled runs stop 7 days after the oldest run in the history (an upstream rule). Run **Actions → Check In** at least once a week. Check In resets the timer by **deleting old run logs**.

GitHub also pauses schedules in public repositories after 60 days without commits; if that happens, re-enable the workflow in the Actions tab.
</details>

---

## 🎯 Choosing topics

**What to collect** is set in `config/config.yaml` → `brightdata:`. Each source has `enabled: true/false`.

```yaml
brightdata:
  serp:    { enabled: true,  search_type: news, recency: day, queries: ["artificial intelligence", "renewable energy"] }
  reddit:  { enabled: true,  keywords: ["artificial intelligence"], subreddits: ["technology", "science"], posts_per_input: 10 }
  x:       { enabled: false, accounts: ["NASA"] }          # X has no keyword search in Bright Data
  youtube: { enabled: false, keywords: ["tech news"] }
  web:     { enabled: false, pages: [{ url: "https://example.com/newsroom", listing: true }] }   # needs BRIGHTDATA_UNLOCKER_ZONE
```

**What makes it into the digest** is set in `config/frequency_words.txt`.
- Items are grouped by your keyword groups; anything that matches no group is dropped.
- In English, use word boundaries: write `/\bai\b/`, otherwise `ai` also matches "s**ai**d".
- To receive everything, leave `[WORD_GROUPS]` empty.

**Optional AI filter:** set `filter.method: "ai"`, describe your interests in `config/ai_interests.txt`, and add `AI_API_KEY`.

---

## 💰 Cost

Bright Data bills **SERP API** and **Web Unlocker** per successful request, and the **Web Scraper API** (Reddit, X, YouTube) per delivered record. Failed requests are not billed.

```
runs/month       = runs per day × 30
SERP requests    = runs/month × queries (×2 if search_type: both)
Web requests     = runs/month × pages
Scraper records  ≤ runs/month × (keywords + subreddits + accounts + channels) × posts_per_input
monthly cost     ≈ (SERP + Web + Scraper records) / 1000 × price per 1,000
```

**Default config:** 3 runs a day, 3 queries, Reddit 1 keyword + 2 subreddits × 10 posts.
- That is ≤ 33 billable units per run, **≤ 2,970 per month**.
- At about **$1.50 per 1,000** (pay-as-you-go, Oct 2026), that is **≈ $4.50/month**.
- Bright Data's free tier (~5,000 credits a month) can cover it.

Prices change, so check [brightdata.com/pricing](https://brightdata.com/pricing).

**Hard limits** in `brightdata.limits` (requests and records, per run and per day) stop a run *before* it goes over. The log shows `usage this run` and `usage today`.

**Actions minutes:** a run waits up to 30 min for scrapers (`brightdata.scraper_max_wait`).
- **Public repo:** free and unlimited.
- **Private repo:** GitHub Free includes 2,000 min/month, so lower the wait or make the repository public. Unfinished jobs are collected by the next run either way.

---

## 📊 How ranking works

```
score = 100 × (0.45·F + 0.35·E + 0.20·R)

F freshness   2^(−hours since published / 12); unknown publish time → half of that, counted from first seen
E engagement  Reddit: upvotes + 2·comments · X: likes + 2·reposts + replies · YouTube: views/100 + likes + 2·comments
              → percentile within the same source and run; Google/listings: position on the page; single page: 0.3
R recurrence  0.6·min(1, (runs seen − 1)/6) + 0.4·min(1, (sources − 1)/2)
```

Example: a Reddit post 3 hours old, more engaged than 85% of the posts collected with it, seen in 2 runs → **70**.

Weights live in `brightdata.ranking`. To go back to the original formula, set `advanced.weight.mode: "legacy"`.

---

## 💻 Run locally

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uv sync
cp .env.example .env              # fill it in; .env is git-ignored
uv run python scripts/preflight.py
uv run python -m trendradar --dry-run
```

A dry run still makes (billed) Bright Data requests, but never sends to Telegram and never saves state.

**Docker:**
1. Copy `docker/.env.example` to `docker/.env` and fill it in.
2. Run `docker compose -f docker/docker-compose.yml up -d --build`. This builds from source; the upstream image has no Bright Data.

---

## 🛠 Troubleshooting

| You see | Do this |
|---|---|
| Preflight: `Missing secret …` | Add it (step 5). Names are case-sensitive. |
| Preflight: Telegram token problem | Copy the token again from @BotFather → `/mybots` → *API Token*. No quotes, no `bot` prefix. |
| `Digest NOT delivered … chat not found` | Press **Start** in your bot. For channels, make the bot an admin. |
| Alert: Bright Data **auth** / **balance** | Check `BRIGHTDATA_API_KEY` and zone names / top up Bright Data. |
| Alert: **limit** reached | Raise `brightdata.limits` or run less often. |
| Green run, no digest | Nothing new matched your groups. Try a dry run with empty `[WORD_GROUPS]`. |
| Scraper job `not ready yet` | Bright Data is slow; the next run collects it. The log shows `status=…`. |
| Schedule stopped | Run **Check In**, then re-enable **Get Hot News**. |

---

## 📜 License, credits, terms

- **Original project:** [**TrendRadar**](https://github.com/sansan0/TrendRadar) © [sansan0](https://github.com/sansan0) and contributors. It provides the filtering, reports, notification channels, AI features and MCP server, documented in [README-EN.md](README-EN.md) / [README-ZH.md](README-ZH.md). Its hot-list data source is [newsnow](https://github.com/ourongxing/newsnow) by ourongxing.
- **This fork (thehypedotnews, November 2026)** adds:
  - the Bright Data source layer (`trendradar/crawler/brightdata/`);
  - the ranking (`trendradar/core/ranking.py`);
  - alerts, dry run and English digest labels (`trendradar/notification/alerts.py`, `i18n.py`);
  - `scripts/preflight.py`;
  - the config, workflow and docs changes above.
- **License:** distributed under **GPL-3.0**, see [LICENSE](LICENSE). All original copyright notices are kept, and modified versions must stay GPL-3.0.
- **Terms:** **each user is responsible for complying with Bright Data's terms of service and the terms of the websites they collect data from.**
