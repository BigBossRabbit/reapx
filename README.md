![ReapX Banner](assets/reapx-banner.svg)

# ReapX

**ReapX — harvest what you saved on X.**

ReapX turns your X (Twitter) bookmarks into reusable AI skills — automatically,
locally, and without the paid X API. Every thread, tool, and link you've saved on
X becomes a ready-to-run Hermes skill.

Built by [BigBossRabbit](https://github.com/BigBossRabbit). ReapX evolved from the
**StarLearner** pipeline and reuses its map → categorize → generate stages; the
bookmark harvesting is what makes it a distinct project.

---

## What it does

- Reads your X bookmarks from your logged-in browser — **any Chromium-family
  browser** (Brave, Chrome, Edge, Arc, Opera), auto-detected
- Maps each tweet into a skill schema (name, description, topics, source URL)
- Categorizes them into 12 domains (AI & ML, Privacy & Security, Dev Tools, etc.)
- Generates polished, ready-to-use `SKILL.md` files in `generated_skills/`

No exporting, no CSV juggling, no manual copying. Cross-platform: **macOS,
Linux, and Windows**.

## How it works — 3 steps

1. **Harvest** your X bookmarks from your local browser session (auto-detected:
   Brave → Chrome → Edge → Arc → Opera — or pass `--browser <name>`).
2. **Map & categorize** each bookmark into a skill schema by domain.
3. **Generate** reusable Hermes skills from templated output.

Run the whole thing with one command:

```bash
bash scripts/daily_sync.sh --source bookmarks
```

If you have several Chromium browsers installed, make sure the auto-picked one is
the one logged into X — or be explicit:

```bash
bash scripts/daily_sync.sh --source bookmarks --browser chrome
# or: REAPX_BROWSER=chrome bash scripts/daily_sync.sh --source bookmarks
```

---

## How the magic works without the X API

The paid X API is not required. ReapX drives your own logged-in session instead:

1. **Drives your logged-in browser session over CDP** — it copies the live
   browser's `Cookies` SQLite file (Brave, Chrome, Edge, Arc, or Opera —
   whichever you have, auto-detected) into a fresh, temporary profile
   (read-only on the source, non-destructive) and launches a headless instance
   on Chrome DevTools Protocol (on a free port, never a hardcoded one). X
   accepts the session exactly as it would the real browser.
2. **Loads your bookmarks in that session** — the headless browser uses the
   copied cookie DB to authenticate, exactly like a real browser (no Keychain
   access needed by the main pipeline). A dead/expired session is detected via
   a login-redirect check after navigation.
3. **Scrolls to load all bookmarks** — it navigates to `https://x.com/i/history`,
   then repeatedly scrolls to the bottom of X's scroll container (detected
   dynamically) to trigger X's infinite load. Every `<article>` is accumulated as
   it passes through the viewport — X virtualizes the timeline so only ~10-15
   articles exist in the DOM at once — and the run stops only when scroll
   height genuinely stops growing (capped at 500 scrolls, 1.8s apart).
4. **Maps tweets to skill schema** — each bookmark is extracted (id, text, author,
   created_at, URLs), deduped by id, and mapped into the `starred_repos.json` schema
   that the categorize + generate stages consume unchanged.

The result: your saved bookmarks become skills, and the whole thing runs on your
machine.

---

## Requirements

- **macOS, Linux, or Windows** — any Chromium-family browser's profile layout is
  auto-detected (the main pipeline needs no Keychain access; only the optional
  `verify_x_bookmarks.py` smoke test does)
- **Any Chromium-family browser** (Brave, Chrome, Edge, Arc, Opera) with a
  **logged-in X session**
- **Python 3.11+**
- pip dependencies from `requirements.txt` (includes the fetcher's
  `websocket-client` — `pip install -r requirements.txt` installs everything):

```bash
pip3 install -r requirements.txt
```

> **macOS Keychain (only for the optional `verify_x_bookmarks.py` smoke test):**
> that script proves the session is decryptable via
> `security find-generic-password -s "Brave Safe Storage"`. macOS will prompt
> *"security wants to access key 'Brave Safe Storage'"* — click **Always Allow**.
> The main pipeline (`daily_sync.sh` / `fetch_x_bookmarks.py`) does **not**
> touch the Keychain: it authenticates by running the copied cookie DB through
> a headless browser over CDP, exactly like a real browser, and detects a dead
> session via a login-redirect check. If the Keychain read hangs or fails,
> only the optional verify step is affected — it fails fast with a clear
> message (10s timeout). To pre-authorize: **Keychain Access** → search
> "Brave Safe Storage" → Get Info → Access Control → add `/usr/bin/security`
> to "Always allow access by these applications".

---

## Quick start

```bash
# 1. Verify your session decrypts and bookmarks fetch
python3 scripts/verify_x_bookmarks.py

# 2. Run the full pipeline on your bookmarks
bash scripts/daily_sync.sh --source bookmarks

# 3. (Optional) scheduled every-3-days runner, emits a single-line summary
bash scripts/run_reapx_3day.sh
```

Outputs:
- `data/x_bookmarks.json` — raw harvested bookmarks
- `data/starred_repos.json` — bookmarks mapped to skill schema
- `data/categorized_repos.json` — domain-categorized items
- `generated_skills/` — generated Hermes skills

---

## Project tree

```
reapx/
├── SKILL.md                    # The ReapX Hermes skill definition
├── README.md
├── LICENSE                     # MIT
├── requirements.txt
├── scripts/
│   ├── daily_sync.sh           # Orchestrates fetch -> map -> categorize -> generate
│   ├── run_reapx_3day.sh       # Every-3-days runner (cron-friendly summary line)
│   ├── fetch_x_bookmarks.py    # Harvests bookmarks via CDP + Keychain (no X API)
│   ├── map_bookmarks_to_repos.py
│   ├── categorize_repos.py
│   ├── generate_skills.py      # Renders skills from Jinja2 templates
│   └── verify_x_bookmarks.py   # Smoke test: session decrypts + bookmarks fetch
├── references/
│   ├── categories.json         # 12 domain categories, keywords, icons
│   └── skill_templates/        # Jinja2 skill templates (default + per-category)
├── data/                       # gitignored — never commit
├── logs/                       # gitignored — never commit
├── generated_skills/           # gitignored — never commit
└── tests/
```

---

## Security & privacy

ReapX is built to be kind to your data:

- **Cookie values are never logged, printed, or committed.** The optional verify
  step only checks for the *presence* of `auth_token`, `ct0`, and
  `_twitter_sess` to confirm the session is usable.
- The source cookie database is opened **read-only** — a copy is made and the
  original browser profile is never touched.
- Session cookies are **never decrypted by the main pipeline** — the headless
  browser authenticates with the copied cookie DB exactly like a real browser,
  so no Keychain access is needed.
- All harvested data, logs, and generated output are **gitignored**
  (`data/*.json`, `logs/*.log`, `generated_skills/`), so nothing personal can be
  committed by accident.

Run it once, run it on a schedule — your bookmarks stay on your machine.

---

## License

[MIT](LICENSE) — Copyright (c) 2026 BigBossRabbit
