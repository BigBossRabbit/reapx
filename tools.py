"""tools.py — the three registered tool handlers (spec A.3).

The contract (dev guide, "Common mistakes"): every handler returns a JSON
string ALWAYS and NEVER raises (anything the driver throws is converted into
a clean error JSON here), takes ``**kwargs`` for forward compatibility, and
the schema's ``description`` is how the model decides when to call it.

There is ONE pipeline tool (``reapx_import``): the plugin takes bookmark
input, never drives a browser. Settings are read from the plugin config
only; the tool args are a fixed allowlist (``path`` / ``items`` /
``generate_limit`` / ``dry_run`` on the pipeline tool, read-only filters on
``reapx_review``). ``reapx_status`` / ``reapx_review`` are read-only.

Lazy everything: the driver (harvest.py) and the engine are imported inside
the handlers — ``register()`` (and the validate probe's recording stub) never
touch them.
"""
from __future__ import annotations

import json

from . import runtime, schemas


def _tool_error(exc: BaseException, **extra) -> str:
    """The never-raise error JSON: exception class + message, no
    tracebacks to the model (A.3.6)."""
    return json.dumps({"ok": False,
                       "error": f"{type(exc).__name__}: {exc}", **extra},
                      ensure_ascii=False)


def import_(args: dict, **kwargs) -> str:
    """reapx_import — the pipeline (a bookmarks file or inline items)."""
    try:
        from . import harvest as _harvest
        args = args or {}
        # The allowlist: per-call data + the two per-call knobs. Anything
        # else a model passes (harvest_mode=, enrich=, …) is ignored —
        # settings are the user's, not the call's.
        declared = {"path": "path", "items": "items",
                    "generate_limit": "generate_limit",
                    "dry_run": "dry_run"}
        clean = {declared[k]: v for k, v in args.items() if k in declared}
        return json.dumps(_harvest.run(clean), ensure_ascii=False)
    except BaseException as exc:  # noqa: BLE001 — never raise
        return _tool_error(exc)


def status(args: dict, **kwargs) -> str:
    """reapx_status (read-only; always registered)."""
    try:
        from . import harvest as _harvest
        from . import skills_out
        state = _harvest._read_run_state()
        result = {"ok": True,
                  "input": "file or inline items (no browser reads)",
                  "import_dir": runtime.setting("import_dir", ""),
                  "last_run": state.get("last_run"),
                  "harvest_running": _harvest.harvest_running(),
                  "enrich": runtime.setting("enrich", "llm"),
                  "llm_max_items": runtime.setting("llm_max_items", 50),
                  "generate_limit": runtime.setting("generate_limit", 5),
                  "register_generated_skills":
                      runtime.setting("register_generated_skills", False)}
        try:
            result["skills"] = skills_out.status()
        except Exception as exc:  # a status read must never fail
            result["skills"] = {"error": f"{type(exc).__name__}"}
        # the ledger + last assessment (the engine's data files)
        try:
            from . import _engine
            _engine.load()
            data = skills_out.data_dir()
            result["ledger"] = _engine.ledger.report(
                _engine.ledger.load_ledger(data / "ledger.json"))
        except Exception as exc:
            result["ledger"] = {"error": f"{type(exc).__name__}"}
        try:
            import json as _json
            from pathlib import Path as _Path
            opp = _Path(skills_out.data_dir()) / "opportunities.json"
            if opp.is_file():
                result["last_assessment"] = (_json.loads(
                    opp.read_text(encoding="utf-8")) or {}).get("counts")
        except Exception:
            pass
        return json.dumps(result, ensure_ascii=False, default=str)
    except BaseException as exc:  # noqa: BLE001
        return _tool_error(exc)


def review(args: dict, **kwargs) -> str:
    """reapx_review (read-only; always registered)."""
    try:
        from . import skills_out
        args = args or {}
        since = args.get("since")
        category = args.get("category")
        opportunities_only = bool(args.get("opportunities_only"))
        include_skill_md = bool(args.get("include_skill_md"))
        last_run = None
        if not since:
            from . import harvest as _harvest
            last_run = (_harvest._read_run_state()
                        .get("last_run") or {}).get("ts")
        try:
            out = skills_out.review_rows(
                since=since or last_run, category=category,
                opportunities_only=opportunities_only,
                include_skill_md=include_skill_md)
        except Exception as exc:  # a read surface never fails (no hermes /
            # an unavailable store ⇒ the empty table, with the reason)
            out = {"items": [],
                   "counts": {"total": 0, "integrated": 0,
                              "opportunities": 0, "llm_flagged": 0},
                   "error": f"review store unavailable "
                            f"({type(exc).__name__})"}
        out["ok"] = True
        out["filters"] = {"since": since or last_run, "category": category,
                          "opportunities_only": opportunities_only,
                          "include_skill_md": include_skill_md}
        return json.dumps(out, ensure_ascii=False, default=str)
    except BaseException as exc:  # noqa: BLE001
        return _tool_error(exc)


HANDLERS = {"reapx_import": import_, "reapx_status": status,
            "reapx_review": review}
