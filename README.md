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

- Reads your X bookmarks from your logged-in Brave browser
- Maps each tweet into a skill schema (name, description, topics, source URL)
- Categorizes them into 12 domains (AI & ML, Privacy & Security, Dev Tools, etc.)
- Generates polished, ready-to-use `SKILL.md` files in `generated_skills/`

No exporting, no CSV juggling, no manual copying.

## How it works — 3 steps

1. **Harvest** your X bookmarks from your local Brave session.
2. **Map & categorize** each bookmark into a skill schema by domain.
3. **Generate** reusable Hermes skills from templated output.

Run the whole thing with one command:

```bash
bash scripts/daily_sync.sh --source bookmarks
```

---

## How the magic works without the X API

The paid X API is not required. ReapX drives your own logged-in session instead:

1. **Drives your logged-in Brave session over CDP** — it copies the live Brave
   `Cookies` SQLite file into a fresh, temporary profile (read-only on the source,
   non-destructive) and launches a headless Brave instance on Chrome DevTools
   Protocol (port 9222). X accepts the session exactly as it would the real browser.
2. **Decrypts session cookies from the Brave cookie store via macOS Keychain** —
   the `auth_token`, `ct0`, and `_twitter_sess` cookies are decrypted (AES-128-CBC,
   key derived with PBKDF2 from the "Safe Storage" Keychain secret) purely to prove
   the session is valid.
3. **Scrolls to load all bookmarks** — it navigates to `https://x.com/i/history`,
   then repeatedly scrolls to the bottom to trigger X's infinite load, collecting
   every `<article>` until no new bookmarks appear (capped at 60 scrolls, 1.8s apart).
4. **Maps tweets to skill schema** — each bookmark is extracted (id, text, author,
   created_at, URLs), deduped by id, and mapped into the `starred_repos.json` schema
   that the categorize + generate stages consume unchanged.

The result: your saved bookmarks become skills, and the whole thing runs on your
machine.

---

## Requirements

- **macOS** — uses Keychain and Brave's profile layout
- **Brave Browser** with a **logged-in X session**
- **Python 3**
- pip dependencies from `requirements.txt`, plus the fetcher's WebSocket client:

```bash
pip3 install -r requirements.txt
pip3 install websocket-client
```

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

- **Cookie values are never logged, printed, or committed.** The pipeline only checks
  for the *presence* of `auth_token` and `ct0` to confirm the session decrypts.
- The source cookie database is opened **read-only** — a copy is made and the
  original Brave profile is never touched.
- Session cookies are decrypted from your **macOS Keychain** (Brave/Chrome "Safe
  Storage") entirely in memory.
- All harvested data, logs, and generated output are **gitignored**
  (`data/*.json`, `logs/*.log`, `generated_skills/`), so nothing personal can be
  committed by accident.

Run it once, run it on a schedule — your bookmarks stay on your machine.

---

## License

[MIT](LICENSE) — Copyright (c) 2026 BigBossRabbit
