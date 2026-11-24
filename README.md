# TrendRadar-thehype

> **Fork of [TrendRadar](https://github.com/sansan0/TrendRadar) by [sansan0](https://github.com/sansan0).**
> Modified by thehypedotnews, November 2026. Licensed under GPL-3.0 like the original (see [LICENSE](LICENSE)).
> The original documentation is kept in [README-EN.md](README-EN.md) (English) and [README-ZH.md](README-ZH.md) (Chinese).

A news radar that runs on **GitHub Actions** for free, collects fresh items through **[Bright Data](https://brightdata.com)**, keeps only the topics you care about and sends a digest to **Telegram** a few times a day.

## What changed compared to the original

- **Data comes from Bright Data instead of newsnow and RSS:**
  - **Google News / Google** search results for your queries (SERP API).
  - **Reddit** posts by keyword or subreddit, **X** posts from accounts, **YouTube** videos by keyword or channel (Web Scraper API).
  - **Any web page** you list, such as a newsroom or a blog (Web Unlocker).

  newsnow and RSS are still in the code, switched off in `config/config.yaml`.
- **New ranking.** Items are ranked by freshness + engagement (likes, comments, views, search position) + recurrence (seen in several runs or on several sources). The original ranked by position on Chinese hot lists. See [How ranking works](#how-ranking-works).
- **Cross-day memory.** Items already collected on an earlier day are not sent again. This state is kept in the GitHub Actions cache, or in your own Cloudflare R2 bucket if you set one up.
- **Cost limits.** You set a maximum number of Bright Data requests and records per run and per day, and every run logs what it used.
- **Failures are never silent:**
  - If Bright Data fails (wrong key, no balance, limit reached), you get a short Telegram alert and the run turns red.
  - A **preflight** step names every missing secret before anything runs.
- **Dry run.** Collects and filters, then prints the digest instead of sending it.
- **English digest.** Telegram digest labels are in English (`app.digest_language: "en"`; set `"zh"` for the original Chinese labels). The HTML report keeps the original labels.
- **Template-friendly defaults:** English config, neutral example topics, UTC timezone, 3 runs a day, AI features off until you add a key.

---

## Contents

- [Setup (step by step, no coding)](#setup)
- [Choosing your topics](#choosing-your-topics)
- [Cost](#cost)
- [How ranking works](#how-ranking-works)
- [Running on your own computer](#running-on-your-own-computer)
- [Troubleshooting](#troubleshooting)
- [License, credits and terms](#license-credits-and-terms)

---

## Setup

You need about 30 minutes and three accounts: **GitHub**, **Telegram** and **Bright Data**. Bright Data needs a balance or its free tier. **Cloudflare R2** is optional (Step 4).

### Step 1 — Create your own copy of this repository

1. At the top of this page click the green **Use this template** button → **Create a new repository**.
2. Pick your account as **Owner** and give the repository a name.
3. Choose **Public** or **Private**:
   - **Public:** Actions minutes are free and unlimited.
   - **Private:** fine too; 3 runs a day use only a few minutes of the free monthly allowance.

   Your secrets stay hidden either way.
4. Click **Create repository**.

> Use **Use this template**, not **Fork**. In forks, GitHub Actions is off by default and scheduled runs are known to misbehave ([upstream issue #606](https://github.com/sansan0/TrendRadar/issues/606)).

> 📷 **Screenshot placeholder:** the "Use this template → Create a new repository" button.

### Step 2 — Telegram bot and chat ID

1. In Telegram, open **[@BotFather](https://t.me/BotFather)** and send `/newbot`. Choose a name and a username ending in `bot`.
2. BotFather replies with a **token** like `123456789:AA...`. This is your `TELEGRAM_BOT_TOKEN`. Keep it secret.
3. Open your new bot and press **Start**, or send it any message.
4. Get your **chat ID**:
   - Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in your browser (put your token in place of `<YOUR_TOKEN>`).
   - Look for `"chat":{"id": 123456789`. That number is your `TELEGRAM_CHAT_ID`.
5. **To post into a channel or group instead:** add the bot to it as an **admin** and post one message there, then open the same link. Channel and group IDs start with `-100`.

> 📷 **Screenshot placeholder:** BotFather conversation showing where the token appears.
>
> 📷 **Screenshot placeholder:** the getUpdates page with the chat id highlighted.

### Step 3 — Bright Data account, API key, zones and balance

1. Sign up at **[brightdata.com](https://brightdata.com)**. New accounts get a monthly free tier (see [Cost](#cost)).
2. **API key:** go to **Account settings → Users and API keys** and create a key. This is your `BRIGHTDATA_API_KEY`.
3. **SERP API zone** (for Google / Google News):
   - In the Bright Data control panel, open **SERP API** and create a zone; the button may say "Get started" or "Add zone". Give it a name, e.g. `serp_api1`.
   - The **zone name** is your `BRIGHTDATA_SERP_ZONE`. You can see it later on the zone's **Overview** tab.
4. **Web Unlocker zone.** Only needed if you enable `brightdata.web` to follow specific web pages:
   - Create a **Web Unlocker** zone the same way.
   - Its name is your `BRIGHTDATA_UNLOCKER_ZONE`.
5. **Reddit, X and YouTube** use the **Web Scraper API**. It needs no zone, only the API key.
6. **Balance:** under **Billing**, add a payment method or check that the free tier is active. When the balance runs out, the next run sends you a Telegram alert instead of a digest.

> 📷 **Screenshot placeholder:** Bright Data "Users and API keys" page.
>
> 📷 **Screenshot placeholder:** creating a SERP API zone, with the zone name highlighted.

### Step 4 — (Optional) Cloudflare R2 bucket and keys

GitHub Actions forgets all files after every run. The history (no repeated items, the daily request limit, recurrence in the ranking) has to live somewhere:

- **Without R2 (default):** it is kept in the GitHub Actions cache. Nothing to set up. GitHub may drop a cache that goes unused for 7 days, for example during a pause; after that the first digest can repeat a few items once.
- **With R2:** it is kept in your own bucket, permanently. R2's free tier is far more than this project needs, but Cloudflare may ask for a card or PayPal to activate R2.

To use R2:

1. Log in to **[dash.cloudflare.com](https://dash.cloudflare.com)** → **R2 Object Storage** → **Create bucket**. Give it a name, e.g. `trendradar`. This is your `S3_BUCKET_NAME`.
2. Find your **Account ID** on the R2 overview page. Your endpoint is `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`, which goes into `S3_ENDPOINT_URL`.
3. Create the access keys:
   - In R2 go to **Manage R2 API Tokens** → **Create API token**.
   - Permission: **Object Read & Write**, limited to your bucket.
4. After creating the token, copy the **Access Key ID** (`S3_ACCESS_KEY_ID`) and the **Secret Access Key** (`S3_SECRET_ACCESS_KEY`). The secret is shown only once.
5. For `S3_REGION` use `auto`.

> 📷 **Screenshot placeholder:** R2 "Create API token" form with Object Read & Write selected.

### Step 5 — Add the secrets to GitHub

In your repository: **Settings → Secrets and variables → Actions → New repository secret**. Add one secret per row. The names must match exactly.

| Secret name | Required? | What it is |
|---|---|---|
| `BRIGHTDATA_API_KEY` | **yes** | Bright Data API key (Step 3) |
| `BRIGHTDATA_SERP_ZONE` | **yes** (if `brightdata.serp` is on, as it is by default) | Name of your SERP API zone |
| `BRIGHTDATA_UNLOCKER_ZONE` | only if `brightdata.web` is on | Name of your Web Unlocker zone |
| `TELEGRAM_BOT_TOKEN` | **yes** | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | **yes** | Your chat / channel ID |
| `S3_BUCKET_NAME` | only with R2 (all four or none) | R2 bucket name |
| `S3_ACCESS_KEY_ID` | only with R2 | R2 Access Key ID |
| `S3_SECRET_ACCESS_KEY` | only with R2 | R2 Secret Access Key |
| `S3_ENDPOINT_URL` | only with R2 | `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `S3_REGION` | only with R2 | `auto` |
| `AI_API_KEY` | only if you turn on AI features | Key for your LLM provider |
| `AI_MODEL` | optional | e.g. `openai/gpt-4o-mini` ([LiteLLM model names](https://docs.litellm.ai/docs/providers)) |

**With the GitHub CLI (optional).** Run these one at a time inside your repository folder. Each command asks for the value, so it never shows up in your shell history:

```bash
gh secret set BRIGHTDATA_API_KEY
gh secret set BRIGHTDATA_SERP_ZONE
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
# only if you use R2:
gh secret set S3_BUCKET_NAME
gh secret set S3_ACCESS_KEY_ID
gh secret set S3_SECRET_ACCESS_KEY
gh secret set S3_ENDPOINT_URL
gh secret set S3_REGION
```

> 📷 **Screenshot placeholder:** the "New repository secret" form.

### Step 6 — Pick your topics

Edit two files directly on GitHub: open the file and click the ✏️ pencil icon. Details are in [Choosing your topics](#choosing-your-topics).

- `config/config.yaml`, section `brightdata:`: **what to collect** (search queries, subreddits, accounts, pages).
- `config/frequency_words.txt`: **what goes into the digest** (keyword groups).

### Step 7 — Enable Actions

1. Open the **Actions** tab of your repository.
2. If you see a button like **"I understand my workflows, go ahead and enable them"**, click it.
3. If **Get Hot News** shows a note that it is disabled, open it and click **Enable workflow**.

> 📷 **Screenshot placeholder:** the Actions tab with the enable button.

### Step 8 — First manual run

1. **Actions** → **Get Hot News** → **Run workflow**.
2. **First time:** tick **Dry run** and click **Run workflow**.
   - Refresh the page and open the run. The **Run crawler** step prints the digest it *would* send.
   - If **Preflight** fails, it lists the secrets that are missing.
3. **When the dry run looks good:** run it again without **Dry run**. The digest arrives in Telegram within a few minutes.
4. **After that, it runs by itself** at **06:17, 12:17 and 18:17 UTC**. To change the times, edit the `cron:` line in `.github/workflows/crawler.yml` (times are always UTC).

> 📷 **Screenshot placeholder:** "Run workflow" dropdown with the Dry run checkbox.
>
> 📷 **Screenshot placeholder:** a digest message in Telegram.

### Step 9 — Check In every 7 days

Scheduled runs stop by themselves 7 days after the oldest run in the history. This rule comes from the original project and saves resources if you forget about it.

- **To keep it running:** **Actions → Check In → Run workflow** at least once a week. You can set a weekly reminder.
- **Check In deletes the old run logs** of your repository; that is how it resets the timer.
- **GitHub's own rule:** GitHub also pauses scheduled workflows in public repositories after 60 days without commits. If that happens, re-enable the workflow in the Actions tab.

---

## Choosing your topics

**1. What to collect.** This is the `brightdata:` section of `config/config.yaml`. Each source has `enabled: true/false`.

```yaml
brightdata:
  serp:                       # Google News: 1 request per query per run
    enabled: true
    search_type: "news"       # news | web | both
    recency: "day"            # hour | day | week
    queries:
      - "artificial intelligence"
      - "renewable energy"
  reddit:                     # 1 request per run + 1 record per post
    enabled: true
    keywords: ["artificial intelligence"]
    subreddits: ["technology", "science"]
    posts_per_input: 10
  x:                          # posts from accounts (X has no keyword search in Bright Data)
    enabled: false
    accounts: ["NASA"]
  youtube:
    enabled: false
    keywords: ["tech news"]
  web:                        # any page (needs BRIGHTDATA_UNLOCKER_ZONE)
    enabled: false
    pages:
      - url: "https://example.com/newsroom"
        listing: true         # the page lists many articles
```

**2. What goes into the digest.** This is `config/frequency_words.txt`.
- Items are grouped by the keyword groups you define there.
- Anything that matches no group is left out.
- In English, use word boundaries: write `/\bai\b/`, not `ai`, or "ai" will also match inside "s**ai**d".
- To receive everything that was collected, leave `[WORD_GROUPS]` empty.

**Optional AI filtering.** Set `filter.method: "ai"`, describe your interests in plain language in `config/ai_interests.txt`, and add the `AI_API_KEY` secret.

---

## Cost

**How Bright Data bills:**
- **SERP API** and **Web Unlocker** charge per **successful request**.
- **Web Scraper API** (Reddit, X, YouTube) charges per **delivered record** (one post or video = one record).
- Failed requests are not billed.

**Monthly estimate:**

```
runs per month  = runs per day × 30
SERP requests   = runs per month × number of queries × (2 if search_type: both, else 1)
Web requests    = runs per month × number of pages
Scraper records ≤ runs per month × (keywords + subreddits + accounts + channels) × posts_per_input
monthly cost    ≈ (SERP requests + Web requests + Scraper records) / 1000 × price per 1,000
```

**Example with the default config** (3 runs a day, 3 Google News queries, Reddit with 1 keyword + 2 subreddits × 10 posts):

| | Per run | Per month (×90 runs) |
|---|---|---|
| SERP requests | 3 | 270 |
| Reddit records (at most) | 30 | 2,700 |
| **Total billable** | ≤ 33 | **≤ 2,970** |

At the pay-as-you-go price of about **$1.50 per 1,000** (Bright Data's pricing pages, October 2026), that is **about $4.50 a month**. Bright Data's free tier of about **5,000 credits a month**, shared between SERP API, Web Unlocker and Web Scraper API, can cover it fully.

Prices change. Check [brightdata.com/pricing](https://brightdata.com/pricing) and your usage in the Bright Data control panel.

**Built-in limits.** `brightdata.limits` in `config/config.yaml` stops a run *before* it would go over:

```yaml
limits:
  max_requests_per_run: 30
  max_requests_per_day: 120
  max_records_per_run: 200
  max_records_per_day: 600
```

Every run logs its usage, e.g. `usage this run: 5 requests, 30 records` and `usage today: 15 requests / 120`. Attempts that fail are counted too, so the limits err on the safe side.

---

## How ranking works

Every collected item gets a score from 0 to 100:

```
score = 100 × (0.45 × F + 0.35 × E + 0.20 × R)

F  freshness   = 2^(−age in hours / 12)          halves every 12 hours
                 no publish time known → half of that, counted from when we first saw it
E  engagement  = 0…1
                 Reddit: upvotes + 2×comments;  X: likes + 2×reposts + replies;
                 YouTube: views/100 + likes + 2×comments
                   → percentile within the same source in this run (log scale)
                 Google / web listings: position on the page, (N − position + 1) / N
                 single web page: 0.3
R  recurrence  = 0.6 × min(1, (runs seen − 1) / 6) + 0.4 × min(1, (sources − 1) / 2)
```

Example: a Reddit post 3 hours old, more engaged than 85% of the posts collected with it, seen in 2 runs, on 1 source: F = 0.84, E = 0.85, R = 0.10, so the score is **70**.

Weights and constants live in `brightdata.ranking` in `config/config.yaml`. To go back to the original formula, set `advanced.weight.mode: "legacy"`.

The same link found by several sources is shown once. Every source that found it counts towards R.

---

## Running on your own computer

You need [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs the right Python version by itself.

```bash
git clone https://github.com/<you>/<your-repo>.git
cd <your-repo>
uv sync
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
# open .env in a text editor and fill in the values
uv run python scripts/preflight.py
uv run python -m trendradar --dry-run
```

- `.env` is in `.gitignore`, so it is never committed.
- Locally, R2 is optional: without it, history is kept in the `output/` folder.
- `--dry-run` still makes real Bright Data requests, and they are billed. They are not added to the daily counter, because a dry run saves no state. It never sends anything to Telegram and never writes to R2.

**Docker:**
1. Copy `docker/.env.example` to `docker/.env` and fill it in.
2. Run `docker compose -f docker/docker-compose.yml up -d --build`. It builds this fork from source; the upstream image has no Bright Data support.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| **Preflight** fails with `Missing secret …` | Add the named secrets (Step 5). Names are case-sensitive. |
| Telegram alert **"Bright Data collection failed … auth"** | Check `BRIGHTDATA_API_KEY` and the zone name secrets. |
| Alert mentions **balance** | Top up your Bright Data balance. |
| Alert says **limit in config.yaml was reached** | Raise `brightdata.limits` or run less often. |
| Run is green but **no digest** | Nothing new matched your keyword groups. In the log, look for `collected N items → M after dedup/filters` and the keyword-matching lines. Try a dry run with an empty `[WORD_GROUPS]`. |
| Scraper step says **not ready after 420s** | Bright Data was slow. The job keeps running on their side. Raise `brightdata.scraper_max_wait` if it repeats. |
| Scheduled runs stopped | Run **Check In** (Step 9), then re-enable **Get Hot News** if GitHub disabled it. |

---

## License, credits and terms

- **License.** Licensed under **GPL-3.0**, see [LICENSE](LICENSE). This is a modified version of [TrendRadar](https://github.com/sansan0/TrendRadar) by sansan0, and all original copyright notices are kept. The modifications (November 2026) are the Bright Data source layer in `trendradar/crawler/brightdata/`, the ranking in `trendradar/core/ranking.py`, alerts and dry run, `scripts/preflight.py`, and the config, workflow and docs changes listed above.
- **Other features.** Everything else (keyword and AI filtering, reports, other notification channels, the MCP server) comes from the original project, which documents it in [README-EN.md](README-EN.md).
- **Terms.** **Each user is responsible for complying with Bright Data's terms of service and with the terms of the websites they collect data from.**
