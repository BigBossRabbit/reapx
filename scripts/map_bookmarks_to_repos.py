#!/usr/bin/env python3
"""map_bookmarks_to_repos.py — map X bookmarks (tweets) into ReapX's
'starred_repos.json' schema so the pipeline (categorize_repos.py +
generate_skills.py) runs unchanged.

Schema mapping (per bookmark/tweet):
  name             <- slug from first URL's repo (if GitHub) else slugified text
  description      <- cleaned tweet text
  language         <- None
  topics           <- hashtags + @mentions extracted from the text
  html_url         <- https://x.com/<author>/status/<id>
  owner            <- {login: <author>}
  stargazers_count <- 0

Reads <data-dir>/x_bookmarks.json (default: the repo's data/; see
reapx_paths.py — the plugin layer maps into its plugin-data dir), writes
<data-dir>/starred_repos.json-compatible output.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# The data-dir seam (reapx_paths). Flat sibling import, standalone-style.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from reapx_paths import resolve_data_dir, add_data_dir_arg

HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")
MENTION_RE = re.compile(r"@([A-Za-z0-9_]+)")
GITHUB_REPO_RE = re.compile(r"github\.com/([^/]+)/([^/?#]+)", re.I)


def slugify(text, fallback="bookmark"):
    """Turn arbitrary text into a safe slug."""
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (s[:80] or fallback)


def repo_slug_from_url(url):
    m = GITHUB_REPO_RE.search(url or "")
    if m:
        return m.group(2).rstrip("/")
    return None


def map_bookmark(b):
    tid = b.get("id")
    text = b.get("text") or ""
    author = b.get("author")
    created_at = b.get("created_at")
    urls = b.get("urls") or []

    # name: GitHub repo from first URL, else slug of text
    name = None
    for u in urls:
        r = repo_slug_from_url(u)
        if r:
            name = r
            break
    if not name:
        name = slugify(text[:80])

    # description: cleaned text (collapse whitespace)
    description = re.sub(r"\s+", " ", text).strip()

    # topics: hashtags (lowercased) + @mentions (login form)
    topics = sorted({h.lower() for h in HASHTAG_RE.findall(text)}) + \
             sorted({m for m in MENTION_RE.findall(text)})

    return {
        "name": name,
        "description": description,
        "language": None,
        "topics": topics,
        "html_url": f"https://x.com/{author}/status/{tid}" if author and tid else "",
        "owner": {"login": author} if author else {"login": "unknown"},
        "stargazers_count": 0,
        "_bookmark_id": tid,
        "_bookmark_created_at": created_at,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="map_bookmarks_to_repos",
        description="Map x_bookmarks.json into the starred_repos.json schema.")
    add_data_dir_arg(parser)
    args = parser.parse_args(argv)

    # The data-dir seam: <data-dir>/x_bookmarks.json -> <data-dir>/starred_repos.json
    # (default: the repo's data/ — the pre-plugin behaviour, unchanged).
    input_file = resolve_data_dir(args.data_dir) / "x_bookmarks.json"
    output_file = resolve_data_dir(args.data_dir) / "starred_repos.json"

    if not input_file.exists():
        sys.exit(f"ERROR: {input_file} not found. Run fetch_x_bookmarks.py first.")
    with open(input_file) as f:
        bookmarks = json.load(f)

    repos = [map_bookmark(b) for b in bookmarks]
    with open(output_file, "w") as f:
        json.dump(repos, f, indent=2, ensure_ascii=False)

    print(f"[map_bookmarks_to_repos] Mapped {len(repos)} bookmarks -> {output_file}")
    print(f"[map_bookmarks_to_repos] (target schema: starred_repos.json, "
          f"{len(repos)} items, stargazers_count=0)")


if __name__ == "__main__":
    main()
