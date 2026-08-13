---
name: reapx
description: "Turn saved X bookmarks into reusable AI skills, no X API."
version: 1.0.0
author: "BigBossRabbit"
platforms: [darwin]
license: MIT
tags: [x, twitter, bookmarks, skills, automation, brave, cdp]
---

# ReapX

Harvest what you saved on X. ReapX turns your X (Twitter) bookmarks into reusable
Hermes AI skills — without paying for the X API.

## When to Use

- You want your bookmarked X threads, tools, and links turned into runnable Hermes skills.
- You need to refresh your skill library from new bookmarks on a schedule.
- You want a one-command pipeline that never asks you to export data manually.

## What it does

ReapX drives your **logged-in Brave session** to read your X bookmarks, maps each
tweet into a skill schema, categorizes them by domain, and generates ready-to-use
`SKILL.md` files. It is fully local and uses **no paid X API**.

## Prerequisites

- **macOS** (uses Keychain + Brave's profile layout)
- **Brave Browser** installed at `/Applications/Brave Browser.app` with a **logged-in X session**
- **Python 3** and pip dependencies (`requirements.txt`)
- The `websocket-client` package for the fetcher: `pip3 install websocket-client`

## How to Run

Run the whole pipeline on your X bookmarks:

```bash
hermes skills run reapx
```

Or invoke the orchestrator directly (bookmarks is the default source):

```bash
bash scripts/daily_sync.sh --source bookmarks
```

Optional scheduled variant — gates itself to run at most once every 3 days and emits
a single-line summary (ready for cron/Telegram):

```bash
bash scripts/run_reapx_3day.sh
```

Smoke-test the cookie decryption + fetch before a full run:

```bash
python3 scripts/verify_x_bookmarks.py
```

## Architecture

```
fetch -> map -> categorize -> generate
```

1. **fetch** — `fetch_x_bookmarks.py` copies the live Brave `Cookies` SQLite into a
   fresh temp profile (read-only on the source), launches headless Brave over CDP
   (port 9222), loads `https://x.com/i/history`, scrolls to exhaust X's infinite
   load, then extracts each `<article>` (id, text, author, created_at, urls) and
   writes `data/x_bookmarks.json` (idempotent, deduped by id, atomic write).
2. **map** — `map_bookmarks_to_repos.py` maps each tweet into the `starred_repos.json`
   schema (name from the first GitHub URL's repo slug, else slugified text;
   topics from hashtags + mentions).
3. **categorize** — `categorize_repos.py` keyword-matches each item against
   `references/categories.json` (12 domains) → `data/categorized_repos.json`.
4. **generate** — `generate_skills.py` renders each category through Jinja2 templates
   in `references/skill_templates` → `generated_skills/`.

## Security Notes

- **Cookie values are never logged, printed, or persisted.** Only names are checked
  (`auth_token`, `ct0`, `_twitter_sess` presence) to prove the session decrypts.
- The source cookie DB is opened **read-only**; a copy is made and the original is never touched.
- Session cookies are decrypted from the macOS Keychain (Brave/Chrome "Safe Storage").
- All scraped data and generated output are **gitignored** (`data/*.json`,
  `logs/*.log`, `generated_skills/`) so nothing personal is committed.

## Troubleshooting

- **"Redirected to login"** → the copied session wasn't accepted. Confirm X is logged
  in in Brave, then re-run.
- **"Required cookie 'auth_token' missing"** → you're not logged in, or the Keychain
  service lookup failed. Re-login to X in Brave and retry.
- **"No usable Keychain service"** → both "Brave Safe Storage" and "Chrome Safe
  Storage" were rejected. Unlock Keychain / re-auth the session.
- **CDP never came up on port 9222** → a stale `bravex_cdp_profile` may be running;
  the script kills it automatically, but free port 9222 if something else binds it.
- **0 bookmarks fetched** → the scroll loop ended without loading articles;
  check the session and retry.

## Pitfalls

- The script **pkills** any process matching `bravex_cdp_profile` and clears
  `/tmp/bravex_cdp_profile` at start/end — don't point that profile path at a live browser.
- It's macOS-specific: the Brave path, cookie layout, and Keychain lookup are hardcoded
  to this machine's environment. Non-macOS will need those constants changed.
- Bookmarks harvested are only as fresh as your last sync — X's `i/history` reflects
  your live bookmarks at fetch time.
- This project evolved from the **StarLearner** pipeline and reuses its
  map → categorize → generate stages unchanged; the bookmark harvesting is what makes
  ReapX distinct.
