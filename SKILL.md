---
name: reapx
description: "Turn saved X bookmarks into reusable AI skills, no X API."
version: 1.0.0
author: "BigBossRabbit"
platforms: [darwin, linux, windows]
license: MIT
tags: [x, twitter, bookmarks, skills, automation, cdp, chromium]
---

# ReapX

Harvest what you saved on X. ReapX turns your X (Twitter) bookmarks into reusable
Hermes AI skills — without paying for the X API.

## When to Use

- You want your bookmarked X threads, tools, and links turned into runnable Hermes skills.
- You need to refresh your skill library from new bookmarks on a schedule.
- You want a one-command pipeline that never asks you to export data manually.

## What it does

ReapX drives your **logged-in Chromium-family browser session** (Brave, Chrome,
Edge, Arc, or Opera — auto-detected) to read your X bookmarks, maps each
tweet into a skill schema, categorizes them by domain, and generates
ready-to-use `SKILL.md` files. It is fully local, cross-platform (macOS, Linux,
Windows), and uses **no paid X API**.

## Prerequisites

- **macOS, Linux, or Windows** with any Chromium-family browser installed
- A **logged-in X session** in that browser (auto-pick: Brave → Chrome → Edge →
  Arc → Opera; override with `--browser <name>`)
- **Python 3.11+** and pip dependencies from `requirements.txt` (includes
  `websocket-client`): `pip3 install -r requirements.txt`
- No Keychain access needed by the main pipeline (only the optional
  `verify_x_bookmarks.py` smoke test uses it)

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

1. **fetch** — `fetch_x_bookmarks.py` copies the live browser `Cookies` SQLite into a
   fresh temp profile (read-only on the source), launches headless Chromium over CDP
   (auto-detected browser on a free port), loads `https://x.com/i/history`, scrolls
   to exhaust X's infinite load, then extracts each `<article>` (id, text, author,
   created_at, urls) and writes `data/x_bookmarks.json` (idempotent, deduped by id,
   atomic write). A dead session is caught by a login-redirect check after
   navigation.
2. **map** — `map_bookmarks_to_repos.py` maps each tweet into the `starred_repos.json`
   schema (name from the first GitHub URL's repo slug, else slugified text;
   topics from hashtags + mentions).
3. **categorize** — `categorize_repos.py` keyword-matches each item against
   `references/categories.json` (12 domains) → `data/categorized_repos.json`.
4. **generate** — `generate_skills.py` renders each category through Jinja2 templates
   in `references/skill_templates` → `generated_skills/`.

## Browser support

Cross-browser by design (v2): the fetcher auto-detects any installed Chromium-family
browser — **Brave, Chrome, Edge, Arc, Opera** — on **macOS, Linux, and Windows** and
uses the first one found. If you have several installed and the auto-pick isn't the
one logged into X, pass `--browser <name>` (or `REAPX_BROWSER=<name>`). Non-default
profiles: `--profile <name>`. There is **no Keychain dependency** in the main
pipeline — the copied cookie DB authenticates through the headless browser exactly
like a real browser.

## Security Notes

- **Cookie values are never logged, printed, or persisted.** Only names are checked
  (`auth_token`, `ct0`, `_twitter_sess` presence) in the optional verify step.
- The source cookie DB is opened **read-only**; a copy is made and the original is never touched.
- Session cookies are never decrypted by the main pipeline (no Keychain access needed).
- All scraped data and generated output are **gitignored** (`data/*.json`,
  `logs/*.log`, `generated_skills/`) so nothing personal is committed.

## Troubleshooting

- **"Redirected to login"** → the copied session wasn't accepted. Confirm X is logged
  in in the selected browser, then re-run (or pass `--browser <name>` to pick the
  right one).
- **0 bookmarks fetched** → the scroll loop ended without loading articles;
  check the session and retry.
- **"No '<browser>' executable found"** → the auto-detected (or requested) browser
  isn't installed; pass `--browser <installed-name>`.
- **verify_x_bookmarks.py fails on Keychain** → that optional smoke test needs
  Keychain access to prove decryptability; approve the "Brave Safe Storage" prompt
  once (or pre-authorize via Keychain Access). It is NOT needed for the main
  pipeline.

## Pitfalls

- The fetcher launches headless with a **free CDP port** (never a hardcoded one) and
  cleans up its temp profile at the end — no leftover processes or `/tmp` profile dirs.
- It is **cross-browser and cross-platform** (macOS/Linux/Windows); the locator
  resolves each browser's profile layout and binary path per OS.
- Bookmarked skills are only as fresh as your last sync — X's `i/history` reflects
  your live bookmarks at fetch time.
- This project evolved from the **StarLearner** pipeline and reuses its
  map → categorize → generate stages unchanged; the bookmark harvesting is what makes
  ReapX distinct.
