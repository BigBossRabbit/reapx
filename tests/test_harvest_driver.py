#!/usr/bin/env python3
"""test_harvest_driver.py — the end-to-end driver tests (A.13 steps 3-5).

THE acceptance battery for the pipeline, all against the fixture
``tests/fixtures/x_bookmarks.sample.json`` (4 bookmarks: an ai-ml one with a
GitHub URL, a bitcoin-lightning one, an uncategorized mountain photo, a
development-tools scraper), a pinned tmp HERMES_HOME (everything the run
writes lands under it — never the real home, never the repo tree):

  * **inline items, rules engine, bit-for-bit** — run(items) with
    enrich=rules: the engine files (starred/new/categorized/opportunities/
    ledger) under plugin-data are produced, nothing LLM-shaped anywhere
    (llm stats zero-error-zero-calls);
  * **ZERO browser reads** (point 1's assert): after any run, the browser
    chain was never imported (no fetch_x_bookmarks, no
    reapx_browser_locator, no websocket) — the plugin reads no credential
    store by construction;
  * **the single-flight lock** — a second concurrent run raises
    HarvestBusy (the tool hands it back as a clean error);
  * **dry_run** — load + diff only: no generation, no ledger update,
    nothing registered;
  * **0-new** — a second run over the same items is the R12-fast early
    return (the seen-ledger never forgets);
  * **the LLM fusion law** (A.4.2) with a mock ctx.llm — keyword hits
    always win; the LLM classifies the uncategorized; a confident
    disagreement is llm_override (review-only); an LLM Track-B hit lands in
    the review store ONLY (never the rules opportunities.json, never the
    ledger);
  * **settings are config-only** — an enrich arg in the call does not
    change the engine; a generate_limit arg cannot exceed the setting;
  * **register_generated_skills=false (the DEFAULT)** — files + review
    rows + registry rows, no register_skill calls; setting it true
    registers;
  * **no bookmark text in the sqlite store** (the review rows carry the
    text head only; the full text stays in the engine's data files).
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "x_bookmarks.sample.json"

PLUGIN_PKG = "reapx_under_test"
HERMES_CHECKOUT = Path("/Users/fromthejump/.hermes/profiles/thanos/"
                       "hermes-agent")

# plugin-data (plugin_db/plugin_data_dir) needs hermes: run under the hermes
# venv with the checkout on the path (mission-A's HAVE_HERMES pattern).
if str(HERMES_CHECKOUT) not in sys.path and HERMES_CHECKOUT.is_dir():
    sys.path.insert(0, str(HERMES_CHECKOUT))
try:
    import hermes_constants  # noqa: F401
    HAVE_HERMES = True
except Exception:
    HAVE_HERMES = False


def _load_plugin(pkg=PLUGIN_PKG):
    if pkg in sys.modules:
        return sys.modules[pkg]
    spec = importlib.util.spec_from_file_location(
        pkg, REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)])
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(REPO_ROOT)]
    sys.modules[pkg] = module
    spec.loader.exec_module(module)
    return module


class FakeLLM:
    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def complete_structured(self, **kwargs):
        self.calls.append(kwargs)
        if self.results:
            item = self.results.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return FakeResult(parsed=None, content_type="text")


class FakeResult:
    def __init__(self, parsed=None, content_type="json"):
        self.parsed = parsed
        self.content_type = content_type


class RecordingCtx:
    """The mock PluginContext: get_config (settings) + llm (the mock) +
    register_skill recording (handler-time registration, the probe's
    WORLD-A mechanism)."""

    def __init__(self, settings=None, llm=None):
        self._settings = settings or {}
        self.llm = llm
        self.registered = []
        self.register_errors = []

    def get_config(self, key, default=None):
        return self._settings.get(key, default)

    def register_skill(self, name, path, *args, **kwargs):
        self.registered.append((str(name), str(path)))


@unittest.skipUnless(HAVE_HERMES,
                     "plugin-data/plugin_db need hermes (run under the "
                     "hermes venv)")
class HarvestDriverTests(unittest.TestCase):
    """Export mode end-to-end (the plugin-data-rooted pipeline)."""

    def setUp(self):
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)
        self.tmp = tempfile.TemporaryDirectory(prefix="reapx-driver-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "hermes-home"
        self.home.mkdir(parents=True)
        # pin the hermes home (everything the run writes lands under it)
        from hermes_constants import (get_hermes_home,
                                      reset_hermes_home_override,
                                      set_hermes_home_override)
        self._token = set_hermes_home_override(self.home)
        self.addCleanup(lambda: reset_hermes_home_override(self._token))
        self.assertEqual(Path(get_hermes_home()), self.home)
        self.plugin = _load_plugin()
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def tearDown(self):
        try:
            self.plugin.runtime.run_unload()
        except Exception:
            pass
        for name in list(sys.modules):
            if name == PLUGIN_PKG or name.startswith(PLUGIN_PKG + "."):
                sys.modules.pop(name, None)

    # ── the inline-items rules run (bit-for-bit) ──────────────────────────
    def test_items_rules_run_end_to_end(self):
        from reapx_under_test import harvest as h
        ctx = RecordingCtx(settings={"enrich": "rules",
                                     "register_generated_skills": True})
        self.plugin.runtime.set_ctx(ctx)
        result = h.run({"items": self.fixture})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["mode"], "export")
        self.assertEqual(result["fetched_total"], 4)
        self.assertEqual(result["new_count"], 4)
        # engine files under plugin-data
        data = self.home / "plugin-data" / "reapx"
        for name in ("x_bookmarks.json", "starred_repos.json",
                     "new_repos.json", "categorized_repos.json",
                     "opportunities.json", "ledger.json"):
            self.assertTrue((data / name).is_file(), name)
        # the fixture's categories (the rules engine, zero LLM): 0003 (the
        # mountain photo) is rules-uncategorized — no keyword hits, no
        # skill generated for it (the fusion test's LLM-applied lever):
        self.assertEqual(result["enrichment"], "rules")
        self.assertEqual(result["llm"]["map"]["errors"], 0)
        cats = result["categories"]
        self.assertEqual(cats, {"ai-ml": 1, "bitcoin-lightning": 1,
                                "development-tools": 1})
        # generated skills (plugin template), registered (probe WORLD A)
        self.assertEqual(result["skills"]["generated"], 3)
        self.assertEqual(len(ctx.registered),
                         len(result["registered_skills"]))
        for qualified in result["registered_skills"]:
            self.assertTrue(qualified.startswith("reapx:"))
            bare = qualified.split(":", 1)[1]
            self.assertIn((bare, mock.ANY), [(n, p) for n, p in
                                             ctx.registered])
        # the generated files carry the plugin Quick Start (no tap language)
        gen = data / "generated_skills"
        for skill_md in gen.glob("*/*/SKILL.md"):
            text = skill_md.read_text(encoding="utf-8")
            self.assertIn('skill_view("reapx:', text)
            self.assertNotIn("hermes skills tap", text)
        # the rules engine's opportunities.json (bit-for-bit shape): the
        # rules verdicts on the pre-map identities — 0001 (ai-integration
        # via 'llm') + 0002 (bitcoin-lightning) Track-B, 0001/0002/0004
        # Track-A integrated (0002 via 'dashboard' in its text):
        opp = json.loads((data / "opportunities.json").read_text())
        self.assertEqual(opp["counts"], {"assessed": 4, "integrated": 3,
                                         "opportunities": 2})
        # the ledger recorded everything (the run succeeded)
        ledger = json.loads((data / "ledger.json").read_text())
        self.assertEqual(len(ledger["items"]), 4)
        # review rows
        self.assertEqual(result["review_rows"], 4)
        # the note (probe-informed)
        self.assertIn("skill_view", result["note"])
        # nothing LLM-shaped
        self.assertEqual(result["llm"]["map"]["batches"], 0)

    def test_zero_browser_reads(self):
        """Point 1's assert: the plugin path never imports the browser chain
        (reapx_browser_locator / fetch_x_bookmarks / websocket)."""
        # a clean engine state first
        for name in ("reapx_browser_locator", "reapx_cdp_launcher", "reapx_paths"):
            sys.modules.pop(name, None)
        for name in list(sys.modules):
            if name.endswith("fetch_x_bookmarks"):
                sys.modules.pop(name, None)
        from reapx_under_test import harvest as h
        from reapx_under_test import _engine
        _engine._LOADED = False
        ctx = RecordingCtx(settings={"enrich": "rules",
                                     "register_generated_skills": True})
        self.plugin.runtime.set_ctx(ctx)
        result = h.run({"items": self.fixture})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("reapx_browser_locator", sys.modules)
        self.assertFalse(any(m.endswith("fetch_x_bookmarks")
                             for m in sys.modules))
        self.assertFalse(any(m.endswith("scripts.reapx_browser_locator")
                             for m in sys.modules))

    def test_second_run_is_zero_new(self):
        from reapx_under_test import harvest as h
        self.plugin.runtime.set_ctx(RecordingCtx(
            settings={"enrich": "rules"}))
        first = h.run({"items": self.fixture})
        self.assertTrue(first["ok"])
        self.assertEqual(first["new_count"], 4)
        second = h.run({"items": self.fixture})
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["new_count"], 0)
        self.assertIn("No new bookmarks", second["note"])

    def test_dry_run_touches_nothing(self):
        from reapx_under_test import harvest as h
        ctx = RecordingCtx(settings={"enrich": "rules",
                                     "register_generated_skills": True})
        self.plugin.runtime.set_ctx(ctx)
        result = h.run({"items": self.fixture, "dry_run": True})
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(result["dry_run"])
        self.assertEqual(ctx.registered, [])  # nothing registered
        data = self.home / "plugin-data" / "reapx"
        self.assertFalse((data / "ledger.json").exists())  # no ledger update
        gen = data / "generated_skills"
        if gen.exists():  # no generation
            self.assertEqual(list(gen.glob("*/*/SKILL.md")), [])

    def test_missing_input_is_a_clean_error(self):
        """No path, no items, no import_dir = import_invalid (a clean
        JSON error, never a prompt, R12)."""
        from reapx_under_test import harvest as h
        self.plugin.runtime.set_ctx(RecordingCtx(settings={}))
        result = h.run({})
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result["error"], "import_invalid")

    def test_a_missing_import_file_is_import_not_found(self):
        from reapx_under_test import harvest as h
        self.plugin.runtime.set_ctx(RecordingCtx(settings={}))
        result = h.run({"path": str(Path(self.tmp.name) / "nope.json")})
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result["error"], "import_not_found")

    def test_single_flight_lock(self):
        from reapx_under_test import harvest as h
        h._FLIGHT["running"] = True
        h._FLIGHT["started"] = "just now"
        self.addCleanup(lambda: h._release())
        with self.assertRaises(h.HarvestBusy):
            h.run({})

    def test_import_validation_errors(self):
        from reapx_under_test import harvest as h
        self.plugin.runtime.set_ctx(RecordingCtx(
            settings={"register_generated_skills": True}))
        result = h.run({"items": [{"id": "", "text": "x"}]})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "import_invalid")

    def test_import_accepts_a_path(self):
        from reapx_under_test import harvest as h
        path = Path(self.tmp.name) / "bookmarks.json"
        path.write_text(json.dumps(self.fixture), encoding="utf-8")
        self.plugin.runtime.set_ctx(RecordingCtx(
            settings={"enrich": "rules"}))
        result = h.run({"path": str(path)})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["fetched_total"], 4)

    def test_import_dir_setting_is_the_fallback_input(self):
        from reapx_under_test import harvest as h
        folder = Path(self.tmp.name) / "inbox"
        folder.mkdir()
        (folder / "x_bookmarks.json").write_text(
            json.dumps(self.fixture), encoding="utf-8")
        self.plugin.runtime.set_ctx(RecordingCtx(
            settings={"enrich": "rules", "import_dir": str(folder)}))
        result = h.run({})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["fetched_total"], 4)

    def test_undeclared_args_do_not_change_the_settings(self):
        """Point 3: a call passing enrich/llm_max_items/register args is
        ignored — only the config counts."""
        from reapx_under_test import harvest as h
        ctx = RecordingCtx(settings={"enrich": "rules"})
        self.plugin.runtime.set_ctx(ctx)
        result = h.run({"items": self.fixture, "enrich": "llm",
                        "register_generated_skills": True,
                        "llm_max_items": 999})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["enrichment"], "rules")  # the setting won
        self.assertEqual(ctx.registered, [])             # still off
        opp = self.home / "plugin-data" / "reapx" / "opportunities.json"
        self.assertTrue(opp.is_file())

    def test_generate_limit_arg_cannot_exceed_the_setting(self):
        from reapx_under_test import harvest as h
        # the setting says 0 (assess only); the call asks for 9:
        self.plugin.runtime.set_ctx(RecordingCtx(
            settings={"enrich": "rules", "generate_limit": 0}))
        capped = h.run({"items": self.fixture, "generate_limit": 9})
        self.assertTrue(capped.get("ok"), capped)
        self.assertEqual(capped["skills"]["generated"], 0)
        self.assertIn("generate_limit=0", capped["skills"]["skipped"])

    def test_register_generated_skills_false_by_default(self):
        from reapx_under_test import harvest as h
        ctx = RecordingCtx(settings={"enrich": "rules"})
        self.plugin.runtime.set_ctx(ctx)
        result = h.run({"items": self.fixture})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(ctx.registered, [])  # NOT registered
        self.assertEqual(result["registered_skills"], [])
        self.assertEqual(result["skills"]["generated"], 3)  # files written
        self.assertEqual(result["review_rows"], 4)  # reviewable
        self.assertIn("register_generated_skills=false", result["note"])
        # the registry rows exist but registered=0
        db = sqlite3.connect(self.home / "plugin-data" / "reapx"
                             / "data.db")
        rows = db.execute("SELECT registered, COUNT(*) FROM skills "
                          "GROUP BY registered").fetchall()
        db.close()
        self.assertEqual(rows, [(0, 3)])

    def test_no_bookmark_text_in_the_sqlite_store(self):
        """The review rows carry the text HEAD only (privacy: the full text
        stays in the engine's data files)."""
        from reapx_under_test import harvest as h
        self.plugin.runtime.set_ctx(RecordingCtx(
            settings={"enrich": "rules"}))
        h.run({"items": self.fixture})
        db = sqlite3.connect(self.home / "plugin-data" / "reapx"
                             / "data.db")
        heads = [r[0] for r in db.execute("SELECT text_head FROM review")]
        db.close()
        self.assertEqual(len(heads), 4)
        for head in heads:
            self.assertLessEqual(len(head or ""), 160)

    # ── the LLM fusion law (A.4.2) ────────────────────────────────────────
    def test_the_fusion_law(self):
        """keyword hits win; the LLM classifies the uncategorized; a
        confident disagreement = llm_override (review-only); an LLM
        Track-B hit = review-only (never the rules opportunities.json,
        never the ledger)."""
        from reapx_under_test import harvest as h

        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

        # map: the LLM re-identifies all 4. THE MOCK IDENTITIES ARE THE
        # TEST'S LEVERS — their rules verdicts (post-map) were verified
        # against the real engine and pin every fusion branch:
        #   0001 "sample-gpt-local-runner" → rules cat development-tools
        #        (topics 'llm' ×3 lose to 'cli' desc+topics), intA True
        #        (cli), oppB ai-integration ('llm' topics);
        #   0002 "channel-ops-notes" → bitcoin-lightning ('channel'),
        #        intA False, oppB None (the CLEAN row — nothing flagged);
        #   0003 "mountain-photo-notes" → uncategorized (the LLM-applied
        #        branch), intA False, oppB None;
        #   0004 "sample-scraper-lib" → development-tools, intA True
        #        (library/scraper), oppB None.
        llm = FakeLLM(results=[
            # map (batch 1: all 4 items)
            FakeResult(parsed={"items": [
                {"id": "1000000000000000001",
                 "name": "sample-gpt-local-runner",
                 "description": "Runs local models behind a clean CLI.",
                 "topics": ["localai", "llm", "cli"]},
                {"id": "1000000000000000002",
                 "name": "channel-ops-notes",
                 "description": "Channel operations notes.",
                 "topics": ["notes", "ops"]},
                {"id": "1000000000000000003",
                 "name": "mountain-photo-notes",
                 "description": "A pleasant photo.",
                 "topics": ["photo", "nature"]},
                {"id": "1000000000000000004",
                 "name": "sample-scraper-lib",
                 "description": "A scraping library.",
                 "topics": ["scraper", "crawler"]},
            ]}),
            # categorize (batch 1)
            FakeResult(parsed={"items": [
                {"id": "1000000000000000001", "category": "ai-ml",
                 "confidence": 0.9},           # DISAGREES (rules said
                                                # development-tools) →
                                                # review-only llm_override
                                                # (the A.4.2 law), keyword
                                                # wins
                {"id": "1000000000000000002", "category": "bitcoin-lightning",
                 "confidence": 0.9},           # agrees (the clean row)
                {"id": "1000000000000000003", "category": "travel-exploration",
                 "confidence": 0.85},          # classifies the uncategorized
                                                # (rules map-identity hit no
                                                # keyword) → APPLIED
                {"id": "1000000000000000004", "category": "development-tools",
                 "confidence": 0.9},           # agrees
            ]}),
            # assess (batch 1)
            FakeResult(parsed={"items": [
                {"id": "1000000000000000001", "integrate": True,
                 "integrate_rationale": "tool-like",
                 "opportunity": None,
                 "opportunity_rationale": ""},
                {"id": "1000000000000000002", "integrate": False,
                 "integrate_rationale": "notes",
                 "opportunity": None, "opportunity_rationale": ""},
                {"id": "1000000000000000003", "integrate": False,
                 "integrate_rationale": "a photo",
                 "opportunity": "ecommerce",   # LLM-ONLY Track-B hit →
                 "opportunity_rationale": "LLM Track-B candidate"},
                {"id": "1000000000000000004", "integrate": False,
                 "integrate_rationale": "LLM says no (rules said yes)",
                 "opportunity": None, "opportunity_rationale": ""},
            ]}),
        ])
        ctx = RecordingCtx(settings={"enrich": "llm",
                                     "register_generated_skills": True}, llm=llm)
        self.plugin.runtime.set_ctx(ctx)
        result = h.run({"items": fixture})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["enrichment"], "llm")

        data = self.home / "plugin-data" / "reapx"

        # THE FUSION — keyword hits always win:
        cats = json.loads((data / "categorized_repos.json").read_text())
        by_name = {}
        for cat, repos in cats["categories"].items():
            for r in repos:
                by_name[r["name"]] = cat
        # …the LLM's confident ai-ml verdict on 0001 DISAGREED with the
        # keyword verdict (development-tools, post-map identity) — the
        # keyword won; nothing LLM moved the directory structure:
        self.assertEqual(by_name.get("sample-gpt-local-runner"),
                         "development-tools")   # keyword wins
        # …0002 (the LLM agreed with its keyword verdict):
        self.assertEqual(by_name.get("channel-ops-notes"),
                         "bitcoin-lightning")
        # …and the LLM's classification of the uncategorized photo APPLIED:
        self.assertEqual(by_name.get("mountain-photo-notes"),
                         "travel-exploration")
        self.assertEqual(by_name.get("sample-scraper-lib"),
                         "development-tools")
        self.assertEqual(cats["uncategorized"], [])
        # llm_map: 4 applied (the LLM's identities); llm_categorize:
        # 1 applied (the uncategorized photo) + 1 llm_override (0001):
        self.assertEqual(result["llm_map"]["applied"], 4)
        self.assertEqual(result["llm_categorize"]["applied"], 1)
        self.assertEqual(result["llm_categorize"]["llm_overrides"], 1)

        # THE ASSESS FUSION — the rules opportunities.json is untouched by
        # the LLM (bit-for-bit: Track-B LLM hits are review-only):
        opp = json.loads((data / "opportunities.json").read_text())
        opp_names = [o["name"] for o in opp["opportunities"]]
        opp_domains = [o["domain"] for o in opp["opportunities"]]
        # …0001's RULES Track-B (ai-integration, via its map topics) is the
        # only one there:
        self.assertEqual(opp_names, ["sample-gpt-local-runner"])
        self.assertEqual(opp_domains, ["ai-integration"])
        # …the LLM's Track-B hit on the photo (ecommerce) is NOT there:
        self.assertNotIn("ecommerce", opp_domains)
        # …and the ledger rows carry the RULES verdicts only:
        ledger = json.loads((data / "ledger.json").read_text())
        for row in ledger["items"].values():
            self.assertIn("opportunity", row)

        # THE REVIEW STORE: everything LLM-shaped is there, flagged:
        from reapx_under_test import skills_out
        review = skills_out.review_rows()
        by_key = {i["id"]: i for i in review["items"]}
        gpt = by_key["tweet:1000000000000000001"]
        self.assertEqual(gpt["engine"], "llm")
        # the LLM-vs-keyword DISAGREEMENT (it said ai-ml, the keyword said
        # development-tools) ORs into llm_override — the A.4.2
        # review-visibility law (a categorize disagreement never reaches
        # the ledger; it is review-visible):
        self.assertTrue(gpt["llm_override"])
        self.assertEqual(gpt["integrate"], True)  # union (both said yes)
        self.assertEqual(gpt["opportunity"], "ai-integration")  # the RULES
        # the clean row — the LLM agreed on every axis → nothing flagged:
        notes = by_key["tweet:1000000000000000002"]
        self.assertEqual(notes["category"], "bitcoin-lightning")  # keyword
        self.assertFalse(notes["llm_override"])
        self.assertFalse(notes["integrate"])
        # the LLM Track-B hit (ecommerce) = review-only:
        photo = by_key["tweet:1000000000000000003"]
        self.assertEqual(photo["category"], "travel-exploration")  # APPLIED
        self.assertTrue(photo["llm_override"])   # the LLM Track-B hit
        self.assertIsNone(photo["opportunity"])  # rules verdict (None)
        # the LLM-false on a rules-true integrate = flagged (fusion floor):
        scraper = by_key["tweet:1000000000000000004"]
        self.assertTrue(scraper["integrate"])  # keyword floor (union)
        self.assertTrue(scraper["llm_override"])
        # opportunities_only surfaces the flagged + rules-opportunity rows
        # (0002 — the clean row, no rules Track-B — is excluded):
        opp_only = skills_out.review_rows(opportunities_only=True)
        self.assertEqual(opp_only["counts"]["total"], 3)
        self.assertEqual(opp_only["counts"]["llm_flagged"], 3)

        # review-only proof at the ledger level — nothing LLM leaked into
        # the ledger rows (0001 keeps its RULES Track-B; 0003, the LLM
        # Track-B item, has none):
        for key, row in ledger["items"].items():
            self.assertIn("opportunity", row)
        self.assertEqual(
            ledger["items"]["tweet:1000000000000000001"]["opportunity"],
            "ai-integration")                    # the RULES verdict
        self.assertIsNone(
            ledger["items"]["tweet:1000000000000000003"]["opportunity"])
        ledger_opps = [r.get("opportunity")
                       for r in ledger["items"].values()]
        self.assertNotIn("ecommerce", ledger_opps)  # LLM-only → absent

        # the map enrichment worked (the LLM's names):
        self.assertEqual(result["llm_map"]["applied"], 4)
        # registered under the LLM's (collision-resolved) names:
        self.assertEqual(len(result["registered_skills"]), 4)
        self.assertTrue(all(q.startswith("reapx:") for q in
                            result["registered_skills"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
