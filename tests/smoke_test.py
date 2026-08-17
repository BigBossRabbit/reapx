#!/usr/bin/env python3
"""smoke_test.py — CI smoke test for the ReapX pipeline.

Runs the parts of the ReapX pipeline that do NOT need a live X session or a
browser (fetch_x_bookmarks.py / verify_x_bookmarks.py require the user's
logged-in Chromium-family browser and cannot run on CI).

Coverage (in-process, no network, stdlib + repo scripts):
  (a) scripts are importable / parse
  (b) map_bookmarks_to_repos.map_bookmark maps a tweet into the repo schema
      (name slug, cleaned description, hashtag+mention topics, html_url, owner)
  (c) categorize_repos.categorize_repo classifies correctly:
      - an "email client" bookmark is NOT mis-classified as ai-ml (word boundary)
      - a bitcoin bookmark lands in bitcoin-lightning
  (d) generate_skills.generate_skill emits a SKILL.md with clean frontmatter:
      starts with '---' at byte 0, no literal '{{' placeholders,
      a real author (not 'GitHub Community'), related_skills == []

Exits 0 only if every assertion passes.
"""

import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import map_bookmarks_to_repos
import categorize_repos
import generate_skills


# ---------------------------------------------------------------- assertions
# Each returns (name, ok, detail).

def test_importable():
    name = "(a) scripts importable / parse"
    try:
        assert callable(map_bookmarks_to_repos.map_bookmark)
        assert callable(categorize_repos.categorize_repo)
        assert callable(categorize_repos.load_categories)
        assert callable(generate_skills.generate_skill)
        return name, True, "map_bookmarks_to_repos, categorize_repos, generate_skills OK"
    except Exception as e:
        return name, False, f"import/parse failed: {e!r}"


def test_map_bookmark():
    name = "(b) map_bookmarks_to_repos maps tweet -> repo schema"
    try:
        # URL-less tweet: name must be slugified from the text.
        fixture = {
            "id": "1234567890",
            "text": "Loving the new #GPT features from @OpenAI!  Great work.",
            "author": "elonmusk",
            "created_at": "2026-08-13T00:00:00Z",
            "urls": [],
        }
        r = map_bookmarks_to_repos.map_bookmark(fixture)
        expected_name = "loving-the-new-gpt-features-from-openai-great-work"
        expected_desc = "Loving the new #GPT features from @OpenAI! Great work."
        checks = {
            "name slug": r["name"] == expected_name,
            "description cleaned": r["description"] == expected_desc,
            # hashtags are lowercased; @mention case is preserved (as coded)
            "topics = hashtags+mentions": r["topics"] == ["gpt", "OpenAI"],
            "html_url": r["html_url"] == "https://x.com/elonmusk/status/1234567890",
            "owner.login": r["owner"]["login"] == "elonmusk",
            "stargazers_count=0": r["stargazers_count"] == 0,
        }
        assert all(checks.values()), {k: v for k, v in checks.items() if not v}

        # GitHub-URL tweet: name should come from the repo slug.
        gh = map_bookmarks_to_repos.map_bookmark({
            "id": "2", "text": "Check this repo", "author": "karpathy",
            "created_at": None, "urls": ["https://github.com/karpathy/nanoGPT"],
        })
        assert gh["name"] == "nanoGPT", gh["name"]
        assert gh["html_url"] == "https://x.com/karpathy/status/2"
        return name, True, "schema mapping OK (slug, desc, topics, url, owner)"
    except Exception as e:
        return name, False, f"mapping failed: {e!r}"


def test_categorize_email_client():
    name = "(c1) 'email client' NOT mis-classified as ai-ml (word boundary)"
    try:
        categories = categorize_repos.load_categories()
        # "email" contains the substring "ai" — a naive substring match would
        # land this in ai-ml; word-boundary matching must not.
        email_repo = {"name": "email-client",
                      "description": "A great email client for power users",
                      "topics": []}
        cat = categorize_repos.categorize_repo(email_repo, categories)
        assert cat != "ai-ml", f"mis-classified as ai-ml (word boundary broken): {cat}"
        return name, True, f"classified as '{cat}' (not ai-ml)"
    except Exception as e:
        return name, False, f"categorize failed: {e!r}"


def test_categorize_bitcoin():
    name = "(c2) bitcoin bookmark lands in bitcoin-lightning"
    try:
        categories = categorize_repos.load_categories()
        btc_repo = {"name": "bitcoin-wallet",
                    "description": "Bitcoin Lightning network payments wallet",
                    "topics": ["bitcoin", "lightning"]}
        cat = categorize_repos.categorize_repo(btc_repo, categories)
        assert cat == "bitcoin-lightning", f"expected bitcoin-lightning, got {cat}"
        return name, True, f"classified as '{cat}'"
    except Exception as e:
        return name, False, f"categorize failed: {e!r}"


def test_generate_skill():
    name = "(d) generate_skills emits clean SKILL.md"
    try:
        repo = {
            "name": "openai-whisper",
            "description": "Robust speech recognition via Whisper",
            "language": "python",
            "topics": ["ai", "ml"],
            "html_url": "https://github.com/openai/whisper",
            "owner": {"login": "openai"},
            "stargazers_count": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            skill_file, status = generate_skills.generate_skill(repo, "ai-ml", out_dir)
            assert skill_file.name == "SKILL.md", skill_file
            text = skill_file.read_text(encoding="utf-8")

            checks = {
                "starts with '---' at byte 0": text.startswith("---"),
                "no literal '{{' placeholder": "{{" not in text,
                "real author (not 'GitHub Community')":
                    "GitHub Community" not in text and 'author: "openai"' in text,
                "related_skills == []": re.search(r"related_skills:\s*\[\s*\]", text) is not None,
            }
            assert all(checks.values()), {k: v for k, v in checks.items() if not v}
        return name, True, f"SKILL.md OK (status={status})"
    except Exception as e:
        return name, False, f"generate_skill failed: {e!r}"


def main():
    tests = [
        test_importable,
        test_map_bookmark,
        test_categorize_email_client,
        test_categorize_bitcoin,
        test_generate_skill,
    ]
    results = []
    for fn in tests:
        n, ok, detail = fn()
        results.append((n, ok))
        print(f"{'PASS' if ok else 'FAIL'}: {n}  [{detail}]")

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\nReapX smoke test: {passed}/{total} passed")
    if passed != total:
        print("FAILED: one or more assertions did not pass.")
        sys.exit(1)
    print("ALL ASSERTIONS PASS — ReapX pipeline intact.")


if __name__ == "__main__":
    main()
