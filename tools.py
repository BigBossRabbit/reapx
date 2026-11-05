"""tools.py — the four registered tool handlers (spec A.3).

The contract (dev guide, "Common mistakes"): every handler returns a JSON
string ALWAYS and NEVER raises (anything the driver throws is converted into
a clean error JSON here), takes ``**kwargs`` for forward compatibility, and
the schema's ``description`` is how the model decides when to call it.

Mode gating (A.3.5): ``reapx_harvest`` is check_fn-hidden when
``harvest_mode=export`` and ``reapx_import`` when it is ``browser`` (the
conditional-availability pattern); the handlers ALSO guard, so a settings
change mid-session can't leave a mode hole. ``reapx_status`` /
``reapx_review`` are always registered (read-only).

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


def harvest(args: dict, **kwargs) -> str:
    """reapx_harvest (browser mode — the product)."""
    try:
        mode = runtime.setting("harvest_mode", "browser", args)
        if mode != "browser":
            return json.dumps({
                "ok": False, "error": "harvest_disabled_in_export_mode",
                "detail": "harvest_mode=export hides reapx_harvest; use "
                          "reapx_import (zero browser reads), or set "
                          "harvest_mode=browser (applies on the next "
                          "register()).",
            }, ensure_ascii=False)
        from . import harvest as _harvest
        return json.dumps(_harvest.run_harvest(args or {}),
                          ensure_ascii=False)
    except BaseException as exc:  # noqa: BLE001 — never raise
        return _tool_error(exc)


def import_(args: dict, **kwargs) -> str:
    """reapx_import (export mode — the strict-R11 fallback)."""
    try:
        mode = runtime.setting("harvest_mode", "browser", args)
        if mode == "browser":
            return json.dumps({
                "ok": False, "error": "import_disabled_in_browser_mode",
                "detail": "harvest_mode=browser hides reapx_import; use "
                          "reapx_harvest, or set harvest_mode=export "
                          "(applies on the next register()).",
            }, ensure_ascii=False)
        from . import harvest as _harvest
        return json.dumps(_harvest.run_import(args or {}),
                          ensure_ascii=False)
    except BaseException as exc:  # noqa: BLE001
        return _tool_error(exc)


def status(args: dict, **kwargs) -> str:
    """reapx_status (read-only; always registered)."""
    try:
        from . import harvest as _harvest
        from . import skills_out
        state = _harvest._read_run_state()
        result = {"ok": True,
                  "harvest_mode": runtime.setting("harvest_mode", "browser"),
                  "browser": runtime.setting("browser", "auto"),
                  "last_run": state.get("last_run"),
                  "harvest_running": _harvest.harvest_running(),
                  "enrich": runtime.setting("enrich", "llm"),
                  "generate_limit": runtime.setting("generate_limit", 5),
                  "register_generated_skills":
                      runtime.setting("register_generated_skills", True)}
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


HANDLERS = {"reapx_harvest": harvest, "reapx_import": import_,
            "reapx_status": status, "reapx_review": review}
