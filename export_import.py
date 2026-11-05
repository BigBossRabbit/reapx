"""export_import.py — the export-mode input layer (spec A.5.3).

Export mode is the strict-R11 fallback: zero browser reads, zero cookies —
the plugin works from a user-supplied bookmarks file (or an inline array the
agent collected itself through its own, already-consented browser surface).

v1.0 supports exactly the documented ``reapx_bookmarks`` format (the fetcher's
own output schema). An ``x_export`` adapter (the X "download your data"
bookmarks file) is a RESERVED name in the tool schema only — it gets written
against a real verified layout, never a guess (spec open item 5).
"""
from __future__ import annotations

import json
from pathlib import Path


class ImportValidationError(ValueError):
    """A bookmarks file/array is not a valid reapx_bookmarks payload."""


def _validate_item(idx: int, item) -> None:
    if not isinstance(item, dict):
        raise ImportValidationError(
            f"items[{idx}] must be an object, got {type(item).__name__}")
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise ImportValidationError(
            f"items[{idx}].id must be a non-empty string "
            f"(the tweet/bookmark id)")
    if not isinstance(item.get("author", ""), str):
        raise ImportValidationError(f"items[{idx}].author must be a string")
    if not isinstance(item.get("text", ""), str):
        raise ImportValidationError(f"items[{idx}].text must be a string")
    created = item.get("created_at")
    if created is not None and not isinstance(created, str):
        raise ImportValidationError(
            f"items[{idx}].created_at must be an ISO string or absent")
    urls = item.get("urls", [])
    if not isinstance(urls, list) or any(not isinstance(u, str) for u in urls):
        raise ImportValidationError(
            f"items[{idx}].urls must be an array of strings")


def validate_bookmarks(items) -> list:
    """Validate a reapx_bookmarks array (list of {id, author, text,
    created_at?, urls}); returns it; raises ImportValidationError."""
    if not isinstance(items, list):
        raise ImportValidationError(
            f"expected a JSON array of bookmarks, got {type(items).__name__}")
    for idx, item in enumerate(items):
        _validate_item(idx, item)
    return items


def load_bookmarks_file(path) -> list:
    """Load + schema-validate a reapx_bookmarks file (A.5.3: produced by the
    standalone fetcher run once, any machine with the logged-in browser, or
    anything else that can produce the array)."""
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise ImportValidationError(f"import file not found: {file_path}")
    try:
        items = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportValidationError(
            f"import file {file_path} is not a reapx_bookmarks array: "
            f"{exc}") from exc
    return validate_bookmarks(items)


def load_items(items) -> list:
    """Schema-validate an inline bookmark array (agent-mediated harvest: the
    agent collected the bookmarks through its own browser surface)."""
    return validate_bookmarks(items)


def load(path=None, items=None) -> list:
    """Exactly one of path/items (path wins); a validated x_bookmarks list."""
    if path:
        return load_bookmarks_file(path)
    if items is not None:
        return load_items(items)
    raise ImportValidationError(
        "reapx_import needs a bookmark file (path) or an inline array (items)")
