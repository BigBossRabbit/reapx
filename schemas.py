"""schemas.py — the three registered tool schemas (spec A.3, verbatim).

Every handler returns a JSON string always and never raises (tools.py). The
``description`` fields are how the model decides when to use a tool.

The arg surface is a DELIBERATE allowlist: settings (enrich, llm_max_items,
import_dir, generate_limit, register_generated_skills) are the user's and are
read from the plugin config only. A call may pass per-call DATA (path/items)
and the two per-call knobs that can only narrow the configured run
(generate_limit, dry_run).
"""
from __future__ import annotations

IMPORT = {
    "name": "reapx_import",
    "description": (
        "Turn X (Twitter) bookmarks into ready-to-load Hermes skills. Takes the "
        "bookmarks as DATA — either an inline `items` array (the agent reads "
        "x.com/i/history with its own browser tool, with the user's consent, "
        "and passes the items in) or a `path` to a bookmarks JSON file. The "
        "plugin itself never touches a browser or a cookie store. Then, "
        "INCREMENTALLY (a persistent seen-ledger): maps each NEW bookmark to a "
        "skill schema, categorizes it into one of 12 domains, runs a two-track "
        "assessment (worth integrating into the user's own makeup / "
        "business-opportunity value), and generates skill files that "
        "`reapx_review` lists (registered namespaced as reapx:<name> only when "
        "the register_generated_skills setting is on). Use when the user "
        "mentions their X bookmarks, saving things on X, or refreshing their "
        "skill library from X."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path":  {"type": "string",
                      "description": "Path to a JSON array of bookmarks in the "
                                     "x_bookmarks.json schema ([{id, author, "
                                     "text, created_at, urls}]). Required unless "
                                     "items is given; falls back to the "
                                     "import_dir setting."},
            "items": {"type": "array", "items": {"type": "object"},
                      "description": "Inline bookmark array (same schema) — what "
                                     "the agent collected itself. Use instead of "
                                     "path (path wins if both are given)."},
            "generate_limit": {"type": "integer", "minimum": 0,
                               "description": "Per-call cap on skills generated "
                                              "per category; may only LOWER the "
                                              "generate_limit setting, never "
                                              "raise it. 0 = assess only."},
            "dry_run": {"type": "boolean", "default": False,
                        "description": "Load + diff + enrich only; no "
                                       "generation, no ledger update, nothing "
                                       "registered."},
        },
        "required": [],
    },
}

STATUS = {
    "name": "reapx_status",
    "description": (
        "Read-only ReapX status: last run, ledger counts (total seen / new "
        "last run), skills generated + registered (namespaced names), last "
        "two-track assessment counts, LLM enrichment state (engine used, "
        "items, errors), and pending review items. Use before/after a run, "
        "or whenever the user asks what ReapX has done or what skills it "
        "gave them."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

REVIEW = {
    "name": "reapx_review",
    "description": (
        "Review what the last run produced, item by item: each new bookmark "
        "with its tweet text, category, two-track assessment (integrate-into-"
        "makeup / business-opportunity + rationale), and the generated skill "
        "(namespaced name + frontmatter head). Supports since/category/"
        "opportunity filters. This is the human-in-the-loop surface of the "
        "two-track assessment — LLM-derived opportunities land here, never "
        "silently in the ledger. Read this before turning "
        "register_generated_skills on: skill bodies come from other people's "
        "tweets."
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

ALL = {s["name"]: s for s in (IMPORT, STATUS, REVIEW)}
