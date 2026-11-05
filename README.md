![ReapX Banner](assets/reapx-banner.svg)

# ReapX

**ReapX — harvest what you saved on X.**

ReapX turns your X (Twitter) bookmarks into reusable AI skills —
automatically, locally, and without the paid X API. Every thread, tool,
and link you've saved on X becomes a ready-to-load Hermes skill:
**`reapx_harvest` → map → categorize → two-track assess → generate →
`skill_view("reapx:<name>")`**. The agent ends the run with capabilities
it did not have.

**Derived from
[starlearner-nexus](https://github.com/BigBossRabbit/starlearner-nexus-hermes-skill).**
ReapX evolved from the StarLearner pipeline, which we also maintain, and
reuses its map → categorize → generate stages verbatim (same author, same
engine files). What makes it a different plugin, not a fork: the
**transport** (your logged-in browser's copied cookie store over CDP
reading x.com/i/history — no paid API — versus the GitHub REST stars
API), the **harvest engine** that transport requires
(`browser_locator`, `cdp_launcher`, the scroll-to-exhaust fetcher, the
bookmark→schema mapper), and the **capability it adds** (X bookmark
capture, session-liveness detection) that StarLearner's API transport
cannot provide. Both projects are submitted by the same owner (R05), this
one second, with this line pointing at the origin entry. Lineage
evidence (from the local git logs of both repos, re-verified at PR
time): the StarLearner repository's initial commit is dated 2026-05-15;
the ReapX repository's initial commit is dated 2026-08-13; the
StarLearner history carries the fork event on the record (a
bookmark-ingestion source was added and reverted, and ReapX's first
feature commit is the same incremental-ledger + two-track feature, same
day, as a merge PR).

---

## Part 1 — the plugin (install it)

A **Hermes plugin** (manifest v2): four tools, one registered auxiliary
LLM task, one bundled index skill, one optional automation blueprint,
zero stored credentials, zero capabilities needed (no override
arguments are ever passed to `ctx.llm`, and no `tools.override`).

```bash
hermes plugins install <this repo>   # or the catalog entry, pinned SHA
```

| Tool | What it does |
|---|---|
| `reapx_harvest` | The product (browser mode): harvest → map → assess → **generate + register skills** (`reapx:<name>`), returned in the result. Long-running (bounded by `max_scrolls`). |
| `reapx_import` | **Export mode** (zero browser reads): the identical pipeline from a bookmarks file/inline array — the strictest-privacy mode. |
| `reapx_status` | Read-only: mode, last run, ledger counts, registered skills, assessment + LLM-enrichment state, pending review items. |
| `reapx_review` | Read-only, item by item: tweet text, category, the two tracks, the generated skill. **Everything LLM-shaped lands here for the human** — never silently in the ledger. |

### The settings (Desktop → Plugins → reapx)

| Setting | Default | Meaning |
|---|---|---|
| `harvest_mode` | `browser` | `browser` = your own logged-in browser (see the R13 section); `export` = zero browser reads, a bookmarks file only. |
| `browser` | `auto` | Which installed Chromium-family browser carries your X login. |
| `browser_profile` | `Default` | Profile directory name. |
| `max_scrolls` | `500` | Hard cap on the scroll loop (use 40–80 for quick runs). |
| `import_dir` | `""` | (export mode) default directory `reapx_import` reads from. |
| `enrich` | `llm` | `llm` = map/categorize/assess run through the host LLM (`ctx.llm` — your active provider and model, **host-owned credentials, zero keys in this plugin**) with the deterministic rules engine as the bit-for-bit fallback; `rules` = zero LLM calls. |
| `llm_max_items` | `50` | Per-run cap on items sent for LLM enrichment (a token-spend guard). `0` disables. |
| `generate_limit` | `5` | Max skills per category per run. `0` = assess-only. |
| `register_generated_skills` | `true` | Register generated skills (`reapx:<name>`, loaded with `skill_view`). If false: files + `reapx_review` only. |

**Registration timing (probed on a live Hermes v0.22.6):** handler-time
`register_skill` is accepted **and live in the same session** — a
`skill_view("reapx:<name>")` on a name returned by a harvest resolves
through the real agent path the same second, the bundle banner lists
live siblings, and the skill appears in `skills_list` + the index on the
next prompt build. A plugin (re)load re-registers everything generated
before it from the `plugin_db` registry (the guaranteed form).

### The two harvest modes (the catalog-rule-11 posture)

- **`browser` (the default, the product):** reads your bookmarks from
  your own logged-in browser. Full disclosure in the R13 section below.
- **`export`:** zero browser reads, zero cookies — the identical
  pipeline from a bookmarks file (`reapx_import.path`) or an inline
  array (`reapx_import.items`, e.g. what the agent itself collected
  through its own, already-consented browser surface).

Both modes are complete, shippable products; the engine below the fetch
step (map/ledger/categorize/assess/generate/register) is identical. The
settings-form row IS the consent-at-configure surface; this README + the
catalog PR disclose everything (R13); and the repo carries a **draft
issue** (`docs/browser-seam-issue-DRAFT.md`) asking the hermes-agent
maintainers for a proper, consented browser-session seam — per the
catalog's own *"we would much rather add a proper seam than list a
patch"*. The day a real `browser.*` capability id exists, browser mode
gates on it (the exact migration is in the draft).

### The LLM layer (`ctx.llm`), and when there is no LLM

The engine today has **zero LLM calls** (regex mapping, weighted-keyword
categorizing, a two-track rules assessor, Jinja2 generation).
`ctx.llm` is a new, optional **enrichment layer** over it:

- **map** — the LLM gives each bookmark a real skill identity (a 3–8
  word name, a plain description) instead of the rules engine's slug.
  The visible quality win.
- **categorize** — **fixed fusion, deterministic:** a keyword hit always
  wins (auditable; drives the directory structure). The LLM classifies
  items the keyword engine left `uncategorized`; a confident LLM verdict
  that *disagrees* with a keyword verdict is recorded as
  `llm_override` **in `reapx_review` for the human** — never applied.
- **assess** — keyword hits are the floor (union); LLM-derived Track-B
  (business-opportunity) hits land in `reapx_review` with their
  rationale and are **never written to the ledger without review**.
- **generate** — never LLM. Jinja2 renders LLM-produced *variables*;
  the LLM never writes the SKILL.md structure.

Zero override arguments are ever passed (`provider=` / `model=` /
`agent_id=` / `profile=` — the trust gate is structurally unreachable);
`task=` is only this plugin's own registered auxiliary task
(`reapx_enricher`, follows your active model until you pin one under
`auxiliary.reapx_enricher`). Every LLM failure — no provider configured,
a 5xx through the host's fallback chain, the 30s timeout, non-JSON or
schema-invalid output (an error class only, never message text) — maps
to ONE behavior: **the rules fallback, never a failure, never a hang.**
`enrich: rules` (or `llm_max_items: 0`, or every call failing) = the
deterministic engine, bit-for-bit. Nothing LLM-shaped is ever mistaken
for deterministic output: every review row carries
`engine: "rules" | "llm"` + the `llm_override` flag, and
`reapx_status.pending_review` counts them.

---

## What this plugin does on your machine (R13 disclosures)

> **Network calls to third-party services**
> - **x.com** (harvest mode only): pages of *your own* account
>   (`x.com/i/history`), read through your own logged-in browser session
>   (a read-only copy of your browser's `Cookies` file, a temporary
>   headless browser, deleted after the run). **No paid X API. No other
>   websites. No telemetry, no usage reporting, no phone-home of any
>   kind.**
> - **Your LLM provider** (only if `enrich: llm`, the default): the
>   map/categorize/assess stages send bookmark text (public tweet
>   content) through the **Hermes host** (`ctx.llm`) to your active
>   provider/model, host-owned credentials — **the plugin never sees or
>   stores any key**. Set `enrich: rules` for zero LLM calls.
> - **GitHub is never contacted** by the plugin (the
>   `--source github-stars` branch is standalone-only).
>
> **Reads outside the plugin's own data**
> - (harvest mode) Your browser's **`Cookies` SQLite file** — read-only,
>   copied to a temporary profile, source never modified. **Never
>   decrypted; no cookie value is ever read, logged, printed, or
>   persisted**; expired logins fail as a clean error.
> - (harvest mode) Browser **install locations** (app bundles / PATH /
>   Windows roots) to find the browser binary; **profile directory
>   names** (never contents) for cookie-store resolution.
> - (export mode) **One file you point `reapx_import` at. Nothing
>   else.**
> - (both) The plugin's own `plugin-data/reapx/` store (ledger,
>   registry, generated skills).
> - **No keychain, no `auth.json`, no `.env`, no other tool's login is
>   ever read.**
>
> **Shell commands / processes**
> - (harvest mode) Launches **your own browser binary, headless**, with
>   `--remote-debugging-port=<random free port>` against the temporary
>   profile. It is terminated (then killed if needed) and the profile
>   deleted in a `finally` block — no orphan processes, no `pkill`, no
>   hardcoded ports. **No other commands are run; no shell-out to other
>   CLIs.**
>
> **Long-running background processes**
> - **None of the plugin's own.** A harvest runs inside a tool call
>   (bounded by `max_scrolls`, default 500) while you watch. The optional
>   **Automation Blueprint "reapx:harvest"** (a ready-made cron job you
>   must explicitly schedule) runs the same pipeline under hermes cron —
>   non-interactive by design: it reports errors (e.g. an expired login)
>   instead of waiting for a person.
>
> **Stored credentials**
> - **None.** The plugin stores no keys, tokens, or cookies. LLM
>   credentials are the host's (`ctx.llm`); the browser session stays in
>   your browser (a deleted temp copy excepted); the X API is not used.
>
> **Data + writes**
> - Everything the plugin writes lives in
>   `<hermes home>/plugin-data/reapx/` (survives plugin updates, follows
>   the active profile): your harvested bookmarks, the seen-ledger,
>   generated skills, assessment output. **Nothing is uploaded
>   anywhere** (except the optional LLM enrichment above). Uninstalling
>   the plugin leaves your data; removing the data dir removes
>   everything.

### Browser access (harvest mode, the default)

To read your X bookmarks without the paid X API, ReapX copies the
`Cookies` SQLite file of your logged-in Chromium-family browser (Brave,
Chrome, Edge, Arc, or Opera) **read-only** into a temporary profile,
launches that browser **headless** on a random free port, and lets it
read `x.com/i/history` exactly as it would when you use it. It then
deletes the temporary profile. The original browser profile is never
modified. **No cookie value is ever decrypted, read, logged, printed, or
persisted** — ReapX copies a file; the browser itself authenticates, as
designed by the browser. An expired login is detected as a redirect and
reported as a clean error. The optional standalone smoke test
`verify_x_bookmarks.py` (not part of the plugin) is the only component
that can touch the macOS Keychain, to prove the session is decryptable.
This is a deliberate design: we treat your logged-in browser session as
**yours**. If you prefer a plugin that never touches the browser at all,
set `harvest_mode: export`.

(Export mode reads one file you point it at and nothing else: no
browser, no cookies, no network beyond the optional LLM enrichment you
configured. It is the strictest-privacy way to run ReapX.)

---

## Part 2 — the standalone (run it by hand)

One tree, two install shapes: the same repo keeps working as a
**standalone skill-pipeline** — `bash scripts/daily_sync.sh`, the
`Makefile` targets, `tests/run_smoke.sh`, `scripts/run_reapx_3day.sh`
cron. **Nothing a standalone user runs changes, breaks, or requires
Hermes.** The plugin layer (`plugin.yaml`, `__init__.py`, `schemas.py`,
`tools.py`, `llm.py`, `skills_out.py`, `harvest.py`,
`export_import.py`, `skills/reapx-index/`) is new files the standalone
never imports; the engine changes that made it possible are **additive
seams** (`--data-dir` with the standalone default = the repo's `data/`,
`--generate-limit` replacing a hardcoded 5, `scripts/__init__.py`,
`plugin_skill.md.j2` beside the standalone template).

- **Standalone writes** go to the repo's `data/`, `logs/`,
  `generated_skills/` (all gitignored). **Plugin writes** go to
  `plugin-data/reapx/` only (never the install tree).
- **What the standalone has that the plugin deliberately doesn't:** the
  Keychain verify smoke test (`verify_x_bookmarks.py`), the
  `--source github-stars` branch (the StarLearner plugin's job), the
  X-graph/CRM research tooling (`fetch_x_graph.py`, `probe_*.py`,
  `merge_*.py` — never plugin-imported), repo-tree writes, the
  standalone skill template's tap language
  (`hermes skills tap add ...` — plugin-generated skills say
  `skill_view("reapx:<name>")` instead).

**The plugin is the same engine, installed properly — the standalone is
the same engine, run by hand.**

### What it does

- Reads your X bookmarks from your logged-in browser — **any
  Chromium-family browser** (Brave, Chrome, Edge, Arc, Opera),
  auto-detected
- Maps each tweet into a skill schema (name, description, topics, source
  URL)
- Categorizes them into 12 domains (AI & ML, Privacy & Security, Dev
  Tools, etc.)
- Generates polished, ready-to-use `SKILL.md` files in
  `generated_skills/`
- **Incremental (only-new) processing**: a persistent seen-ledger means
  each run only counts and assesses bookmarks you have not already taken
  into account
- **Two-track assessment**: every new bookmark is scored for (a)
  integrating into your own makeup and (b) business-opportunity value,
  surfaced in `data/opportunities.json`

No exporting, no CSV juggling, no manual copying. Cross-platform:
**macOS, Linux, and Windows**.

### 🔁 Incremental Processing (seen-ledger)

ReapX is **incremental by design**. It keeps a persistent ledger at
`data/ledger.json` (gitignored) that records every bookmark already
taken into account. On each run:

1. **Harvest** the full current set of bookmarks (unchanged).
2. **Diff** against the ledger → only **NEW** bookmarks advance.
3. Only new bookmarks are **categorized**, **assessed** and turned into
   skills.
4. New bookmarks are recorded into the ledger **only after a successful
   run**, so a mid-run failure never marks items as seen.

The result: a bookmark you have already processed is never counted
again. Each run reports **how many are new**, what was integrated, and
what is a business opportunity — not the total.

```bash
# See how many bookmarks have been seen so far
python3 scripts/ledger.py report data/ledger.json
```

The ledger keys on the tweet `_bookmark_id` (a stable identity), so
re-saves and re-fetches are handled correctly.

### How to run it (3 steps)

1. **Harvest** your X bookmarks from your local browser session
   (auto-detected: Brave → Chrome → Edge → Arc → Opera — or pass
   `--browser <name>`).
2. **Map & categorize** each bookmark into a skill schema by domain.
3. **Generate** reusable Hermes skills from templated output.

Run the whole thing with one command:

```bash
bash scripts/daily_sync.sh --source bookmarks
```

If you have several Chromium browsers installed, make sure the
auto-picked one is the one logged into X — or be explicit:

```bash
bash scripts/daily_sync.sh --source bookmarks --browser chrome
# or: REAPX_BROWSER=chrome bash scripts/daily_sync.sh --source bookmarks
```

### How the magic works without the X API

The paid X API is not required. ReapX drives your own logged-in session
instead:

1. **Drives your logged-in browser session over CDP** — it copies the
   live browser's `Cookies` SQLite file (Brave, Chrome, Edge, Arc, or
   Opera — whichever you have, auto-detected) into a fresh, temporary
   profile (read-only on the source, non-destructive) and launches a
   headless instance on Chrome DevTools Protocol (on a free port, never
   a hardcoded one). X accepts the session exactly as it would the real
   browser.
2. **Loads your bookmarks in that session** — the headless browser uses
   the copied cookie DB to authenticate, exactly like a real browser
   (no Keychain access needed by the main pipeline). A dead/expired
   session is detected via a login-redirect check after navigation.
3. **Scrolls to load all bookmarks** — it navigates to
   `https://x.com/i/history`, then repeatedly scrolls to the bottom of
   X's scroll container (detected dynamically) to trigger X's infinite
   load. Every `<article>` is accumulated as it passes through the
   viewport — X virtualizes the timeline so only ~10-15 articles exist
   in the DOM at once — and the run stops only when scroll height
   genuinely stops growing (capped at 500 scrolls, 1.8s apart).
4. **Maps tweets to skill schema** — each bookmark is extracted (id,
   text, author, created_at, URLs), deduped by id, and mapped into the
   `starred_repos.json` schema that the categorize + generate stages
   consume unchanged.

The result: your saved bookmarks become skills, and the whole thing runs
on your machine.

### Requirements

- **macOS, Linux, or Windows** — any Chromium-family browser's profile
  layout is auto-detected (the main pipeline needs no Keychain access;
  only the optional `verify_x_bookmarks.py` smoke test does)
- **Any Chromium-family browser** (Brave, Chrome, Edge, Arc, Opera) with
  a **logged-in X session**
- **Python 3.11+**
- pip dependencies from `requirements.txt` (includes the fetcher's
  `websocket-client` — `pip install -r requirements.txt` installs
  everything):

```bash
pip3 install -r requirements.txt
```

> **macOS Keychain (only for the optional `verify_x_bookmarks.py` smoke
> test):** that script proves the session is decryptable via
> `security find-generic-password -s "Brave Safe Storage"`. macOS will
> prompt *"security wants to access key 'Brave Safe Storage'"* — click
> **Always Allow**. The main pipeline (`daily_sync.sh` /
> `fetch_x_bookmarks.py`) does **not** touch the Keychain: it
> authenticates by running the copied cookie DB through a headless
> browser over CDP, exactly like a real browser, and detects a dead
> session via a login-redirect check. If the Keychain read hangs or
> fails, only the optional verify step is affected — it fails fast with
> a clear message (10s timeout). To pre-authorize: **Keychain Access**
> → search "Brave Safe Storage" → Get Info → Access Control → add
> `/usr/bin/security` to "Always allow access by these applications".

### Quick start (standalone)

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
- `data/new_repos.json` — only the NEW bookmarks this run (ledger diff)
- `data/categorized_repos.json` — domain-categorized items
- `data/opportunities.json` — two-track assessment (integration +
  business opportunity)
- `data/ledger.json` — persistent seen-ledger (incremental dedup)
- `generated_skills/` — generated Hermes skills

(Plugin mode: the identical engine files land in
`<hermes home>/plugin-data/reapx/` instead, and the generated skills are
namespaced + registered, not written to `generated_skills/`.)

### Project tree

```
reapx/
├── plugin.yaml                # the plugin manifest v2 (Part 1)
├── __init__.py                # register() — the plugin surface
├── schemas.py · tools.py      # the 4 tools + their JSON schemas
├── llm.py                     # the ctx.llm enrichment layer (A.4)
├── skills_out.py              # the generated-skills subsystem (A.7)
├── harvest.py                 # the in-process pipeline driver
├── export_import.py           # the export-mode input layer
├── SKILL.md                   # The ReapX standalone skill definition
├── README.md · LICENSE (MIT) · requirements.txt
├── skills/reapx-index/        # the bundled reapx:index skill
├── scripts/
│   ├── daily_sync.sh          # Orchestrates fetch -> map -> categorize -> generate
│   ├── run_reapx_3day.sh      # Every-3-days runner (cron-friendly summary line)
│   ├── fetch_x_bookmarks.py   # Harvests bookmarks via CDP (no X API)
│   ├── map_bookmarks_to_repos.py · categorize_repos.py
│   ├── assess_repos.py · ledger.py
│   ├── generate_skills.py     # Renders skills from Jinja2 templates
│   ├── reapx_paths.py         # the --data-dir seam (standalone default = data/)
│   └── verify_x_bookmarks.py  # Smoke test: session decrypts + bookmarks fetch
│                               # (standalone-only; the one Keychain touch)
├── references/
│   ├── categories.json        # 12 domain categories, keywords, icons
│   └── skill_templates/       # default (standalone) + plugin templates
├── docs/                      # the browser-seam issue DRAFT (A.5.6)
├── data/ · logs/ · generated_skills/   # gitignored — never commit
└── tests/
```

### Security & privacy

ReapX is built to be kind to your data:

- **Cookie values are never logged, printed, or committed.** The
  optional verify step only checks for the *presence* of `auth_token`,
  `ct0`, and `_twitter_sess` to confirm the session is usable.
- The source cookie database is only ever **copied read-only** — the
  original browser profile is never touched.
- Session cookies are **never decrypted by the main pipeline** — the
  headless browser authenticates with the copied cookie DB exactly like
  a real browser, so no Keychain access is needed.
- All harvested data, logs, and generated output are **gitignored**
  (`data/*.json`, `logs/*.log`, `generated_skills/`), so nothing
  personal can be committed by accident.
- The browser subprocess (harvest mode) is always your own binary,
  headless, on a random free port, with a temporary profile that is
  always deleted (`terminate` → wait → `kill`; never `pkill`).
- No paid X API, no X API keys, no GitHub calls (plugin mode), no
  telemetry, no data leaving the machine (the optional LLM enrichment
  excepted, disclosed above).
- Generated skills are markdown rendered from templates over your own
  bookmark text; nothing generated is executable; `reapx_review` exists
  precisely so a human can read what was generated before it is relied
  on.

Run it once, run it on a schedule — your bookmarks stay on your machine.

## License

[MIT](LICENSE) — Copyright (c) 2026 BigBossRabbit
