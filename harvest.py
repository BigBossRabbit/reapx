"""harvest.py — the in-process pipeline driver (spec A.3.1/A.5).

What ``scripts/daily_sync.sh`` orchestrates as a shell pipeline, run
in-process inside a tool handler — the same engine stages, in the same
order, into the host-owned ``plugin_data_dir("reapx")`` (A.2.1.2's seam):

  ① fetch (browser mode only)      fetch_x_bookmarks.main(argv)
  ② map                            map_bookmarks.main(["--data-dir", …])
  ③ ledger diff (0 new ⇒ exit)     ledger.diff_new / write new_repos.json
  ④ llm.enrich_map                 (A.4.2 map: the LLM identity pass)
  ⑤ skills_out.resolve_collisions  (A.7.2's law, BEFORE the generate)
       → rewrite new_repos.json (the final identities)
  ⑥ categorize (rules)            categorize_repos.categorize_repos(in, out)
  ⑦ llm.enrich_categorize          (A.4.2's fixed fusion — see below)
  ⑧ assess (rules)                 assess_repos' pure functions (the driver
                                    replicates its main(): the rules engine's
                                    opportunities.json, bit-for-bit)
  ⑨ llm.enrich_assess              (A.4.2's fusion — see below)
  ⑩ generate (Jinja2, NO LLM)      generate_skills.generate_skills(
                                       in, out, generate_limit, plugin_skill.md.j2)
  ⑪ skills_out.write_and_register  (registry + handler-time register_skill +
                                    the review rows)
  ⑫ ledger update (success only)   ledger.update_ledger (a mid-run failure
                                    never marks items as seen)

**The categorize fusion (A.4.2, fixed + deterministic):** a keyword hit
always wins (it drives the directory structure); the LLM classifies (a)
items the keyword engine left ``uncategorized`` and (b) items whose keyword
score is 1 (weak); an LLM verdict (confidence ≥ 0.8) that DISAGREES with a
strong keyword verdict is recorded as ``llm_override`` for the human
(reapx_review), never applied.

**The assess fusion (A.4.2):** keyword hits are the floor (union) for Track
A (an LLM-true on a rules-false item adds a *candidate*, flagged
engine=llm/llm_override for review); LLM-derived Track-B hits land in the
review store ONLY — never in the rules opportunities.json (bit-for-bit) and
never in the ledger without review.

**enrich=rules (or llm_max_items=0, or every LLM failure) ⇒ the current
engine, bit-for-bit**: steps ④⑤(renames)/⑦⑨ collapse into the rules
output (verified by test_harvest_driver's rules-mode fixture run vs the
standalone engine output).

**The error model (A.3.1, R12)**: every failure = a clean ``{"ok": false,
"error": <kind>, "detail": …}`` — cookie_db_not_found (installed browsers +
"or switch harvest_mode=export"), login_redirect (dead session: re-login to
X in <browser> and re-run), no_browser_executable, cdp_up_timeout,
dependency_missing, harvest_already_running, import_* (export mode),
llm_unavailable (NOT an error: enrichment=rules + llm.errors) — never a
prompt, never a hang, nothing that waits for a person.

The single-flight lock makes cron + interactive + a curious model call the
harvest twice safe.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path

from . import runtime

# ── the single-flight lock (A.3.5) ───────────────────────────────────────────

_FLIGHT = {"running": False, "started": None, "mode": None}
_FLIGHT_LOCK = threading.Lock()


class HarvestBusy(RuntimeError):
    """A second concurrent harvest (clean error JSON, R12)."""


def _acquire(mode: str) -> float:
    with _FLIGHT_LOCK:
        if _FLIGHT["running"]:
            started = _FLIGHT["started"]
            raise HarvestBusy(
                f"harvest already running (started {started})")
        _FLIGHT["running"] = True
        _FLIGHT["started"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime())
        _FLIGHT["mode"] = mode
        return time.time()


def _release() -> None:
    with _FLIGHT_LOCK:
        _FLIGHT["running"] = False
        _FLIGHT["started"] = None
        _FLIGHT["mode"] = None


def harvest_running() -> bool:
    with _FLIGHT_LOCK:
        return bool(_FLIGHT["running"])


# ── the fetch error model (A.3.1) ────────────────────────────────────────────


class HarvestError(RuntimeError):
    """A mapped harvest failure (kind + fix, never a traceback)."""

    def __init__(self, kind: str, detail: str = "", extra: dict | None = None):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.detail = detail
        self.extra = extra or {}


def _map_fetch_error(exc: BaseException) -> HarvestError:
    """Map the engine's RuntimeErrors onto the A.3.1 error kinds."""
    text = str(exc) or type(exc).__name__
    if "Cookie DB not found" in text or "Source cookie DB not found" in text:
        installed = "unavailable"
        try:
            import sys as _sys
            _scripts = str(Path(__file__).resolve().parent / "scripts")
            if _scripts not in _sys.path:
                _sys.path.insert(0, _scripts)
            # browser_locator is a reapx-exclusive top-level name (see
            # _engine); a lazy guarded import never fails the error path.
            from browser_locator import get_installed_browsers  # type: ignore
            installed = ", ".join(get_installed_browsers() or ["none"])
        except Exception:
            installed = "unavailable"
        return HarvestError(
            "cookie_db_not_found", text,
            {"installed_browsers": installed,
             "fix": "is the right one logged into X? or switch "
                    "harvest_mode=export (zero browser reads)"})
    if "Redirected to login" in text:
        return HarvestError(
            "login_redirect", text,
            {"fix": "the copied session was not accepted — re-login to X in "
                    "the selected browser and re-run"})
    if "executable found" in text:
        return HarvestError("no_browser_executable", text)
    if "CDP endpoint never came up" in text:
        return HarvestError("cdp_up_timeout", text)
    if "websocket" in text.lower():
        return HarvestError(
            "dependency_missing", text,
            {"fix": "install the plugin's python dependencies "
                    "(hermes plugins install resolves them; run hermes pm "
                    "repair + restart if the plugin was just enabled)"})
    return HarvestError("engine_error", text)


# ── the run record (reapx_status' data) ──────────────────────────────────────


def _run_state_path() -> Path:
    from . import skills_out
    return skills_out.data_dir() / "run_state.json"


def _read_run_state() -> dict:
    try:
        return json.loads(_run_state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_run_state(state: dict) -> None:
    path = _run_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False,
                              default=str), encoding="utf-8")
    tmp.replace(path)


# ── the driver ───────────────────────────────────────────────────────────────

# The A.3.1 note, written from the probe's answer (never from hope).
NOTE = ("New skills are registered and loadable now with "
        "skill_view(\"reapx:<name>\"), listed by reapx_status/reapx_review, "
        "and appear in skills_list + the skills index on the next prompt "
        "build; a plugin (re)load re-registers them from the plugin_db "
        "registry (the guaranteed form).")

NOTE_RULES = ("No LLM enrichment was applied (enrich=rules): the "
              "deterministic rules engine ran bit-for-bit. Generated skills "
              "are loadable now with skill_view(\"reapx:<name>\").")

NOTE_EXPORT = ("Export mode: zero browser reads (the bookmarks came from a "
               "file/inline array). New skills are loadable now with "
               "skill_view(\"reapx:<name>\").")


class _Pipeline:
    """One harvest run (browser or export mode)."""

    def __init__(self, args: dict, mode: str):
        self.args = dict(args or {})
        self.mode = mode
        from . import skills_out
        self.so = skills_out
        self.data = skills_out.data_dir()
        self.gen_root = self.data / "generated_skills"
        # engine files under the plugin data dir (A.2.1.2's seam)
        self.x_bookmarks = self.data / "x_bookmarks.json"
        self.starred = self.data / "starred_repos.json"
        self.new_repos = self.data / "new_repos.json"
        self.categorized = self.data / "categorized_repos.json"
        self.opportunities = self.data / "opportunities.json"
        self.ledger_path = self.data / "ledger.json"
        # settings (args > settings > defaults)
        self.enrich = runtime.setting("enrich", "llm", self.args) or "llm"
        self.llm_max_items = int(runtime.setting("llm_max_items", 50,
                                                 self.args) or 50)
        self.generate_limit = int(runtime.setting("generate_limit", 5,
                                                  self.args) or 5)
        self.register_skills = bool(runtime.setting("register_generated_skills",
                                                    True, self.args))
        self.dry_run = bool(self.args.get("dry_run"))
        self.result: dict = {"ok": True, "mode": mode}
        # the enrichment layers' outputs (set as the run progresses)
        self._llm_map: dict = {}
        self._llm_cat: dict = {}
        self._llm_assess: dict = {}
        self.assess_data: dict = {}

    # ── ① fetch (browser mode) ──────────────────────────────────────────
    def fetch(self):
        from . import _engine
        # EVERYTHING inside the error-mapping try (load_fetch included: a
        # missing websocket-client must map to dependency_missing, never
        # escape as a SystemExit/RuntimeError into the internal catch).
        try:
            _engine.load_fetch()
            argv = ["--output", str(self.x_bookmarks)]
            browser = runtime.setting("browser", "auto", self.args)
            profile = runtime.setting("browser_profile", "Default",
                                      self.args)
            max_scrolls = runtime.setting("max_scrolls", 500, self.args)
            if browser:
                argv += ["--browser", str(browser)]
            if profile:
                argv += ["--profile", str(profile)]
            if max_scrolls:
                argv += ["--max-scrolls", str(int(max_scrolls))]
            _engine.fetch_x_bookmarks.main(argv)
        except HarvestError:
            raise
        except SystemExit as exc:  # the fetcher's missing-dep exit
            raise _map_fetch_error(exc) from exc
        except BaseException as exc:  # RuntimeError etc.
            raise _map_fetch_error(exc) from exc
        total = self._count(self.x_bookmarks)
        self.result["fetched_total"] = total
        self.result["browser"] = str(browser or "auto")

    # ── ①' export-mode input (A.5.3) ────────────────────────────────────
    def load_input(self):
        from . import export_import
        path = self.args.get("path")
        items = self.args.get("items")
        if not path and not items:
            import_dir = runtime.setting("import_dir", "", self.args)
            if import_dir:
                path = str(Path(import_dir).expanduser()
                           / "x_bookmarks.json")
        try:
            bookmarks = export_import.load(path=path, items=items)
        except export_import.ImportValidationError as exc:
            raise HarvestError("import_invalid", str(exc)) from exc
        self.data.mkdir(parents=True, exist_ok=True)
        tmp = self.x_bookmarks.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(bookmarks, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.x_bookmarks)
        self.result["fetched_total"] = len(bookmarks)
        self.result["browser"] = None

    # ── ② map ───────────────────────────────────────────────────────────
    def map(self):
        from . import _engine
        _engine.load()
        try:
            _engine.map_bookmarks_to_repos.main(["--data-dir",
                                                 str(self.data)])
        except SystemExit as exc:  # "ERROR: … not found" (missing input)
            raise HarvestError("map_failed", str(exc)) from exc
        self.result["mapped_total"] = self._count(self.starred)

    # ── ③ ledger diff (0 new ⇒ early return) ────────────────────────────
    def diff(self) -> list:
        from . import _engine
        items = self._load(self.starred)
        ledger = _engine.ledger.load_ledger(self.ledger_path)
        new = _engine.ledger.diff_new(items, ledger)
        self._dump(self.new_repos, new)
        self.result["new_count"] = len(new)
        self.result["ledger"] = _engine.ledger.report(ledger)
        return new

    # ── ④ llm.enrich_map + ⑤ collisions → the final identities ─────────
    def enrich_and_resolve(self, new: list):
        from . import llm as llm_mod
        from . import skills_out
        llm_map, map_stats = llm_mod.enrich_map(
            new, llm_max_items=self.llm_max_items, enrich=self.enrich)
        applied = 0
        for item in new:
            key = skills_out.item_source_key(item)
            override = llm_map.get(key)
            if override:
                item["name"] = override["name"]
                item["description"] = override["description"]
                item["topics"] = override["topics"]
                item["_map_engine"] = "llm"
                applied += 1
        # The collision law (A.7.2) — BEFORE the generate (the resolved
        # name is the generator's input).
        db = skills_out._db()
        skills_out._init_db(db)
        renames = skills_out.resolve_collisions(new, db)
        db.close()
        # Rewrite new_repos.json with the final identities.
        self._dump(self.new_repos, new)
        self.result["llm_map"] = {"applied": applied,
                                  **map_stats.as_dict()}
        self.result["collisions"] = len(renames)
        if renames:
            self.result["collision_renames"] = [
                {"key": k, **v} for k, v in renames.items()]
        return llm_map, map_stats

    # ── ⑥ categorize (rules) ────────────────────────────────────────────
    def categorize(self):
        from . import _engine
        _engine.categorize_repos.categorize_repos(
            str(self.new_repos), str(self.categorized))

    # ── ⑦ llm.enrich_categorize + the fixed fusion (A.4.2) ──────────────
    def enrich_categorize(self, new: list):
        from . import llm as llm_mod
        from . import skills_out
        cat_data = self._load(self.categorized)
        rules_cats = dict(cat_data.get("categories") or {})
        uncategorized = list(cat_data.get("uncategorized") or [])
        verdicts, cat_stats = llm_mod.enrich_categorize(
            new, llm_max_items=self.llm_max_items, enrich=self.enrich)

        # The rules verdict per item (keyword hits).
        rules_cat_by_key = {}
        for cat, repos in rules_cats.items():
            for repo in repos:
                rules_cat_by_key[skills_out.item_source_key(repo)] = cat
        for repo in uncategorized:
            rules_cat_by_key[skills_out.item_source_key(repo)] = \
                "uncategorized"

        applied = 0
        overrides = 0
        for item in new:
            key = skills_out.item_source_key(item)
            rules_cat = rules_cat_by_key.get(key) or "uncategorized"
            llm_verdict = verdicts.get(key)
            final_cat = rules_cat
            engine = "rules"
            llm_override = 0
            if llm_verdict:
                llm_cat = llm_verdict["category"]
                conf = llm_verdict["confidence"]
                # The fusion: a keyword hit always wins; the LLM classifies
                # the uncategorized (and, defensively, weak-scoring items);
                # a confident DISAGREEMENT with a keyword verdict is a
                # review-only llm_override, never applied.
                if rules_cat == "uncategorized" and llm_cat != \
                        "uncategorized":
                    final_cat = llm_cat
                    engine = "llm"
                    applied += 1
                elif (rules_cat != "uncategorized"
                      and llm_cat != rules_cat
                      and conf >= 0.8):
                    llm_override = 1
                    overrides += 1
                elif rules_cat != "uncategorized" and llm_cat == rules_cat:
                    engine = "llm"
            item["_category"] = final_cat
            item["_cat_engine"] = engine
            item["_llm_override"] = llm_override
            item["_llm_cat"] = llm_verdict["category"] if llm_verdict else None

        # Re-bucket the categorized file with the fusion's verdicts.
        final_cats: dict = {}
        for item in new:
            final_cats[skills_out.item_source_key(item)] = item["_category"]
        merged: dict = {}
        for cat in list(rules_cats.keys()):
            merged[cat] = [r for r in rules_cats[cat]
                           if final_cats.get(skills_out.item_source_key(r))
                           == cat]
        for item in new:
            cat = item["_category"]
            if cat == "uncategorized":
                continue
            if cat not in merged:
                merged[cat] = []
            keys_in_cat = {skills_out.item_source_key(r)
                           for r in merged[cat]}
            key = skills_out.item_source_key(item)
            if key not in keys_in_cat:
                merged[cat].append(item)
        merged.pop("uncategorized", None)
        cat_data["categories"] = merged
        cat_data["uncategorized"] = [
            item for item in new if item["_category"] == "uncategorized"]
        cat_data["metadata"]["categorized_count"] = sum(
            len(v) for v in merged.values())
        cat_data["metadata"]["uncategorized_count"] = len(
            cat_data["uncategorized"])
        self._dump(self.categorized, cat_data)
        self.result["llm_categorize"] = {"applied": applied,
                                         "llm_overrides": overrides,
                                         **cat_stats.as_dict()}
        self.result["categories"] = {
            cat: len(repos) for cat, repos in sorted(merged.items())
            if repos} or {"uncategorized": len(cat_data["uncategorized"])}
        return verdicts, cat_stats

    # ── ⑧ assess (rules, replicated bit-for-bit) ────────────────────────
    def assess(self, new: list):
        from . import _engine
        from . import skills_out
        assess_mod = _engine.assess_repos
        integrated, opportunities = [], []
        for repo in new:
            name = repo.get("full_name") or repo.get("name") or "unknown"
            url = repo.get("html_url") or ""
            stars = repo.get("stargazers_count") or 0
            integ = assess_mod.assess_integration(repo)
            domain, rationale = assess_mod.assess_opportunity(repo)
            if integ:
                integrated.append({"name": name, "url": url, "stars": stars})
            if domain:
                opportunities.append({
                    "name": name, "url": url, "stars": stars,
                    "domain": domain, "rationale": rationale})
        import datetime
        result = {
            "generated_at": datetime.datetime.now().isoformat(),
            "counts": {
                "assessed": len(new),
                "integrated": len(integrated),
                "opportunities": len(opportunities),
            },
            "integrated": integrated,
            "opportunities": opportunities,
        }
        self._dump(self.opportunities, result)
        # _by_key: the review rows' rules verdicts (LLM-derived Track-B
        # never reaches the opportunities list — review only).
        by_key = {}
        for repo in new:
            key = skills_out.item_source_key(repo)
            integ = assess_mod.assess_integration(repo)
            domain, rationale = assess_mod.assess_opportunity(repo)
            by_key[key] = {"integrated": integ,
                           "opportunity": domain,
                           "opportunity_rationale": rationale}
            repo["_a_integrate"] = integ
            repo["_a_opportunity"] = domain
        result["_by_key"] = by_key
        self.assess_data = result
        self.result["opportunities"] = dict(result["counts"])

    # ── ⑨ llm.enrich_assess (the fixed fusion) ──────────────────────────
    def enrich_assess(self, new: list):
        from . import llm as llm_mod
        from . import skills_out
        verdicts, stats = llm_mod.enrich_assess(
            new, llm_max_items=self.llm_max_items, enrich=self.enrich)
        # The fusion (A.4.2) is applied in skills_out.write_and_register's
        # review rows (keyword floor / union for A candidates / review-only
        # for B) — the rules opportunities.json stays bit-for-bit.
        for item in new:
            verdict = verdicts.get(skills_out.item_source_key(item))
            if verdict:
                item["_assess_engine"] = "llm"
        self.result["llm_assess"] = stats.as_dict()
        return verdicts, stats

    # ── ⑩ generate (Jinja2, no LLM) ─────────────────────────────────────
    def generate(self):
        from . import _engine
        from . import runtime as _rt
        if self.generate_limit <= 0:
            self.result["skills"] = {"generated": 0, "unchanged": 0,
                                     "per_category": {},
                                     "skipped": "generate_limit=0 "
                                                "(assess-only)"}
            return {}
        stats = _engine.generate_skills.generate_skills(
            str(self.categorized), str(self.gen_root),
            generate_limit=self.generate_limit,
            template_name=_rt.PLUGIN_TEMPLATE)
        self.result["skills"] = {
            "generated": stats.get("skills_generated", 0),
            "unchanged": stats.get("skills_unchanged", 0),
            "per_category": {cat: c.get("skills_generated", 0)
                             for cat, c in (stats.get("categories")
                                            or {}).items()},
        }
        return stats

    # ── ⑪ skills_out ────────────────────────────────────────────────────
    def write_and_register(self, new: list, gen_stats: dict):
        from . import skills_out
        ctx = runtime.get_ctx()
        llm_map = getattr(self, "_llm_map", None) or {}
        llm_cat = getattr(self, "_llm_cat", None) or {}
        llm_assess = getattr(self, "_llm_assess", None) or {}
        # llm_cat rows carry the fusion markers for the review rows.
        marked_cat = {}
        for item in new:
            key = skills_out.item_source_key(item)
            if item.get("_cat_engine") == "llm" or item.get("_llm_override"):
                marked_cat[key] = {
                    "category": item.get("_llm_cat"),
                    "confidence": 1.0,
                    "_applied": item.get("_cat_engine") == "llm",
                    "_llm_override": item.get("_llm_override", 0)}
        out = skills_out.write_and_register(
            ctx=ctx, items=new,
            categories={skills_out.item_source_key(i): i.get("_category")
                        for i in new},
            assess_data=getattr(self, "assess_data", {}),
            llm_map=llm_map or {}, llm_cat=marked_cat,
            llm_assess=llm_assess or {}, gen_stats=gen_stats or {},
            register_skills=self.register_skills)
        self.result["skills"].update({
            "generated": out.get("generated", 0),
            "unchanged": out.get("unchanged", 0),
            "per_category": out.get("per_category", {}),
            "registry_errors": out.get("errors", [])})
        # A.3.1: the registered names are a TOP-LEVEL result field — the
        # namespaced names the agent reads out of the tool result and
        # skill_views (result["skills"] = the generation counts only).
        self.result["registered_skills"] = out.get("registered", [])
        self.result["review_rows"] = out.get("review_rows", 0)

    # ── ⑫ ledger update (success only) ──────────────────────────────────
    def update_ledger(self, new: list):
        from . import _engine
        ledger = _engine.ledger.load_ledger(self.ledger_path)
        _engine.ledger.update_ledger(ledger, new, source="x-bookmarks")
        # The review verdicts ride the ledger rows (rules values only —
        # LLM-derived Track-B stays review-only).
        from . import skills_out
        for item in new:
            key = skills_out.item_source_key(item)
            row = ledger.get("items", {}).get(key)
            a_map = (getattr(self, "assess_data", {})
                     .get("_by_key", {}).get(key, {}))
            if row is not None:
                row["integrated"] = bool(a_map.get("integrated"))
                row["opportunity"] = a_map.get("opportunity")
        _engine.ledger.save_ledger(ledger, self.ledger_path)
        self.result["ledger"] = _engine.ledger.report(ledger)

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _count(path: Path) -> int:
        try:
            return len(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return 0

    @staticmethod
    def _load(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _dump(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False,
                                  default=str), encoding="utf-8")
        tmp.replace(path)


# ── the entry points (tools.py calls these) ──────────────────────────────────


def run_harvest(args: dict | None = None) -> dict:
    """reapx_harvest (browser mode): the A.3.1 handler flow."""
    args = dict(args or {})
    _acquire("browser")
    try:
        return _run(args, "browser")
    finally:
        _release()


def run_import(args: dict | None = None) -> dict:
    """reapx_import (export mode): step ① replaced by the input load."""
    args = dict(args or {})
    _acquire("export")
    try:
        return _run(args, "export")
    finally:
        _release()


def _run(args: dict, mode: str) -> dict:
    p = _Pipeline(args, mode)
    enrich = p.enrich
    try:
        # ① fetch / load input
        if mode == "browser":
            p.fetch()
        else:
            p.load_input()

        # ② map
        p.map()

        # ③ ledger diff — 0 new ⇒ the R12-fast early return
        new = p.diff()
        p.result["run_id"] = time.strftime("%Y%m%dT%H%M%SZ",
                                           time.gmtime())
        if not new:
            p.result["new_count"] = 0
            p.result["note"] = ("No new bookmarks since the last run "
                                "(the seen-ledger never forgets). Nothing "
                                "was processed.")
            _record(p, ok=True)
            return p.result

        # dry run: fetch + diff (+ enrich) only
        if p.dry_run:
            llm_map, map_stats = p.enrich_and_resolve(new)
            p.result["dry_run"] = True
            p.result["note"] = ("Dry run: fetched + diffed + enriched only; "
                                "no generation, no ledger update, nothing "
                                "registered.")
            _record(p, ok=True)
            return p.result

        # ④⑤ llm.enrich_map + the collision law
        llm_map, map_stats = p.enrich_and_resolve(new)
        p._llm_map = llm_map

        # ⑥ categorize (rules)
        p.categorize()

        # ⑦ llm.enrich_categorize + the fixed fusion
        llm_cat, cat_stats = p.enrich_categorize(new)
        p._llm_cat = llm_cat

        # ⑧ assess (rules)
        p.assess(new)

        # ⑨ llm.enrich_assess (the fixed fusion)
        llm_assess, assess_stats = p.enrich_assess(new)
        p._llm_assess = llm_assess

        # ⑩ generate (Jinja2, no LLM)
        gen_stats = p.generate()

        # ⑪ skills_out (registry + handler-time registration + review)
        p.write_and_register(new, gen_stats)

        # ⑫ ledger update — ONLY on full success
        p.update_ledger(new)

        # the result (A.3.1). enrichment = "llm" iff some LLM output was
        # actually APPLIED (map identities / a category fusion verdict / an
        # assess verdict); anything else (failures, the cap, rules mode) is
        # the rules engine, with llm.errors counted (A.4.3).
        llm_any = (enrich == "llm"
                   and (p.result.get("llm_map", {}).get("applied", 0) > 0
                        or p.result.get("llm_categorize", {}).get("applied",
                                                                  0) > 0
                        or len(p._llm_assess or {}) > 0))
        p.result["enrichment"] = "llm" if llm_any else "rules"
        p.result["llm"] = {"map": p.result.get("llm_map", {}),
                           "categorize": p.result.get("llm_categorize", {}),
                           "assess": p.result.get("llm_assess", {})}
        p.result["note"] = (NOTE if p.register_skills else
                            "register_generated_skills=false: files written "
                            "+ reviewable via reapx_review, not registered.")
        if p.result["enrichment"] == "rules" and enrich == "rules":
            p.result["note"] = (NOTE_RULES if p.register_skills else
                                "register_generated_skills=false.")
        if mode == "export":
            p.result["note"] = (NOTE_EXPORT if p.register_skills else
                                "register_generated_skills=false.")
        p.result["new_sample"] = _sample(new)
        _record(p, ok=True)
        return p.result

    except HarvestBusy:
        raise
    except HarvestError as exc:
        err = {"ok": False, "error": exc.kind, "mode": mode}
        if exc.detail:
            err["detail"] = exc.detail
        if exc.extra:
            err.update(exc.extra)
        p.result.update(err)
        _record(p, ok=False)
        return err
    except Exception as exc:  # never raise out of the driver
        runtime.log.error("reapx harvest failed: %s", exc, exc_info=True)
        err = {"ok": False, "error": "internal",
               "mode": mode, "detail": f"{type(exc).__name__}: {exc}"}
        p.result.update(err)
        _record(p, ok=False)
        return err


def _sample(new: list, limit: int = 5) -> list:
    """A.3.1's new_sample: id, author, text head, category, the two tracks."""
    out = []
    for item in new[:limit]:
        out.append({
            "id": item.get("_bookmark_id") or item.get("id"),
            "author": item.get("author")
                      or (item.get("owner") or {}).get("login"),
            "text_head": (item.get("description") or "")[:100],
            "category": item.get("_category") or "uncategorized",
            "integrate": bool((item.get("_a_integrate"))),
            "opportunity": item.get("_a_opportunity"),
        })
    return out


def _record(p: _Pipeline, ok: bool) -> None:
    """The run record (reapx_status' last_run)."""
    state = _read_run_state()
    state["last_run"] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": p.mode, "ok": ok,
        "error": None if ok else p.result.get("error"),
        "detail": None if ok else p.result.get("detail"),
        "fetched_total": p.result.get("fetched_total"),
        "new_count": p.result.get("new_count", 0),
        "enrichment": p.result.get("enrichment",
                                   "rules" if ok else None),
        "llm": p.result.get("llm", {}),
        "dry_run": p.dry_run,
    }
    state["harvest_mode"] = p.mode
    state["register_generated_skills"] = p.register_skills
    try:
        _write_run_state(state)
    except Exception:
        runtime.log.warning("reapx: run-state write failed", exc_info=True)
