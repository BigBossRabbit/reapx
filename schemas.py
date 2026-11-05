"""schemas.py — the four registered tool schemas (spec A.3, verbatim).

Every handler returns a JSON string always and never raises (tools.py). The
``description`` fields are how the model decides when to use a tool.
"""
from __future__ import annotations

HARVEST = {
    "name": "reapx_harvest",
    "description": (
        "Harvest the user's X (Twitter) bookmarks from their own logged-in "
        "Chromium-family browser (Brave/Chrome/Edge/Arc/Opera) WITHOUT the paid "
        "X API: the live browser's Cookies SQLite is copied read-only into a "
        "temporary profile and a headless browser reads the bookmark history "
        "exactly like the real one (nothing is decrypted, logged or persisted). "
        "Then, INCREMENTALLY (a persistent seen-ledger): maps each NEW bookmark "
        "to a skill schema, categorizes it into one of 12 domains, runs a "
        "two-track assessment (worth integrating into the user's own makeup / "
        "business-opportunity value), and generates ready-to-load skills "
        "(registered namespaced as reapx:<name>, returned in the result). "
        "Long-running (scroll loop, minutes) — use max_scrolls to bound it. "
        "Use when the user mentions their X bookmarks, saving things on X, or "
        "refreshing their skill library from X."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "browser":    {"type": "string",
                           "enum": ["auto", "brave", "chrome", "edge", "arc", "opera"],
                           "description": "Override the settings browser (default settings value)."},
            "profile":    {"type": "string",
                           "description": "Browser profile directory name (default settings value)."},
            "max_scrolls": {"type": "integer", "minimum": 1, "maximum": 500,
                           "description": "Cap on the infinite-scroll loop (default settings; "
                                          "use 40-80 for a quick run of recent bookmarks)."},
            "enrich":     {"type": "string", "enum": ["llm", "rules"],
                           "description": "One-run override of the enrichment engine "
                                          "(default settings value)."},
            "generate_limit": {"type": "integer", "minimum": 0, "maximum": 25,
                           "description": "Max skills generated per category this run "
                                          "(default settings value)."},
            "dry_run":    {"type": "boolean", "default": False,
                           "description": "Fetch + diff + enrich only; no generation, "
                                          "no ledger update, nothing registered."},
        },
        "required": [],
    },
}

IMPORT = {
    "name": "reapx_import",
    "description": (
        "Process X bookmarks WITHOUT reading the browser: from a bookmarks file "
        "or an inline list in the exact x_bookmarks.json schema "
        "([{id, author, text, created_at, urls}]). Runs the identical incremental "
        "pipeline (ledger diff → map/categorize/assess → generate → register). "
        "Zero browser reads, zero cookies — the strictest-privacy mode. Use when "
        "harvest_mode=export, when the user has a bookmarks export, or when the "
        "agent itself has collected the bookmarks (e.g. via its own browser "
        "tools) and passes them in."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path":  {"type": "string",
                      "description": "Path to a file containing a JSON array of bookmarks "
                                     "in the x_bookmarks.json schema (required unless "
                                     "items is given)."},
            "items": {"type": "array", "items": {"type": "object"},
                      "description": "Inline bookmark array (same schema) — e.g. what the "
                                     "agent collected itself."},
            "format": {"type": "string", "enum": ["reapx_bookmarks"],
                      "default": "reapx_bookmarks",
                      "description": "v1.0 supports the documented reapx_bookmarks "
                                     "format only (an X-data-export adapter is a "
                                     "verified-layout build item, not a guess)."},
            "enrich": {"type": "string", "enum": ["llm", "rules"]},
            "generate_limit": {"type": "integer", "minimum": 0, "maximum": 25},
            "dry_run": {"type": "boolean", "default": False},
        },
        "required": [],
    },
}

STATUS = {
    "name": "reapx_status",
    "description": (
        "Read-only ReapX status: harvest mode, last run, ledger counts "
        "(total seen / new last run), skills generated + registered "
        "(namespaced names), last two-track assessment counts, LLM enrichment "
        "state (engine used, items, errors), and pending review items. Use "
        "before/after a harvest, or whenever the user asks what ReapX has done "
        "or what skills it gave them."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

REVIEW = {
    "name": "reapx_review",
    "description": (
        "Review what the last harvest produced, item by item: each new bookmark "
        "with its tweet text, category, two-track assessment (integrate-into-"
        "makeup / business-opportunity + rationale), and the generated skill "
        "(namespaced name + frontmatter head). Supports since/category/opportunity "
        "filters. This is the human-in-the-loop surface of the two-track "
        "assessment — LLM-derived opportunities land here, never silently in "
        "the ledger."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "since":      {"type": "string", "description": "ISO datetime; review items "
                                "first seen after this (default: last run)."},
            "category":   {"type": "string", "description": "One of the 12 domain keys."},
            "opportunities_only": {"type": "boolean", "default": False},
            "include_skill_md":   {"type": "boolean", "default": False,
                                   "description": "Include each generated SKILL.md's "
                                                  "first 40 lines."},
        },
        "required": [],
    },
}

ALL = {s["name"]: s for s in (HARVEST, IMPORT, STATUS, REVIEW)}
