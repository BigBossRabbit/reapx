"""ReapX — the X-bookmarks → Hermes skills plugin (root package).

``register(ctx)`` is the whole plugin surface Hermes loads (A.2.4). Its
contract, proven by the mission-C probe (scratch/reapx-probe) and enforced by
``hermes plugins validate`` (the recording-stub probe) and ``doctor`` (the
network-blocked runtime):

  * touches ONLY ``ctx.get_config`` / ``ctx.register_*`` / ``ctx.on_unload``;
    NEVER ``ctx.llm`` / ``ctx.state`` / ``ctx.manifest`` (the validate
    probe's recording stub raises AttributeError for that surface — the llm
    facade is reached lazily, inside tool handlers, through the context
    captured here);
  * no engine import at register time (the engine is imported through
    ``_engine`` lazily, at first tool call) — registration stays cheap, so
    the 10s load deadline, the doctor's socket blocks and the install
    scanner never see a browser/CDP stack;
  * no network, no subprocess, no writes outside ``plugin_data_dir`` —
    nothing here can hang an unattended start (R12);
  * every optional step (aux task, startup skill scan, blueprint, on_unload)
    is individually guarded — a failure degrades one feature (logged), never
    the load;
  * THE PROBE ANSWER (A.7.4): handler-time ``ctx.register_skill`` IS
    accepted and the skill is live in the same session
    (``skill_view("reapx:<name>")`` resolves it through the real agent path)
    — the harvest handlers call it through ``runtime.get_ctx()``. The
    register()-time ``skills_out.register_all()`` scan of the plugin_db
    registry remains the guaranteed next-session form (a fresh process
    re-registers everything generated before it).

One tree, two install shapes (A.12): the same repo keeps working as the
standalone skill-pipeline (``bash scripts/daily_sync.sh``, the Makefile
targets, ``tests/run_smoke.sh``, ``scripts/run_reapx_3day.sh`` cron) —
nothing a standalone user runs changes, breaks, or requires Hermes.
"""
from __future__ import annotations

from pathlib import Path

from . import runtime, schemas

# Toolset + the mode-gated conditional availability (A.3.5).
TOOLSET = "reapx"


def _mode(setting: str, default: str = "browser") -> str:
    """The current harvest_mode (register()-time + check_fn-time read;
    changes apply on the next register())."""
    return runtime.setting(setting, default) or default


def register(ctx) -> None:
    """The plugin's whole registration surface (see the module docstring)."""
    # 0. the captured context: handler-time ctx.register_skill (A.7.4 probe
    #    WORLD A) + settings reads run through it. Set FIRST.
    runtime.set_ctx(ctx)
    log = runtime.log

    # 1. settings (cheap, no network). Never ctx.llm / ctx.state / ctx.manifest.
    mode = _mode("harvest_mode")

    # 2. our own auxiliary LLM task (zero consent: a plugin may only use a
    #    task it registered itself; operators pin it under
    #    auxiliary.reapx_enricher — it follows the compression slot until
    #    they do). KEY MUST BE ALNUM+UNDERSCORE (a hyphen raises ValueError
    #    and fails the whole load).
    try:
        ctx.register_auxiliary_task(
            runtime.AUX_TASK,
            display_name="ReapX Enricher",
            description="Map/categorize/assess enrichment for the ReapX "
                        "harvest (bookmark text → skill metadata). Follows "
                        "your active model until you pin one.",
            inherit_from="compression",
            defaults={"timeout": 60},
        )
    except Exception:  # a failed aux task degrades llm enrichment to rules
        log.warning("reapx: register_auxiliary_task(%r) failed",
                    runtime.AUX_TASK, exc_info=True)

    # 3. the four tools (A.3). check_fn hides-but-registers per the
    #    conditional-availability pattern (registered set == declared set ==
    #    provides_tools; the visible set follows harvest_mode); the
    #    handlers also guard (belt and braces).
    from . import tools

    ctx.register_tool(
        name="reapx_harvest", toolset=TOOLSET, schema=schemas.HARVEST,
        handler=tools.harvest,
        check_fn=lambda: _mode("harvest_mode") == "browser",
    )
    ctx.register_tool(
        name="reapx_import", toolset=TOOLSET, schema=schemas.IMPORT,
        handler=tools.import_,
        check_fn=lambda: _mode("harvest_mode") == "export",
    )
    ctx.register_tool(
        name="reapx_status", toolset=TOOLSET, schema=schemas.STATUS,
        handler=tools.status,
    )
    ctx.register_tool(
        name="reapx_review", toolset=TOOLSET, schema=schemas.REVIEW,
        handler=tools.review,
    )

    # 4. generated-skills registration (A.7): the startup scan of the
    #    plugin_db registry — THE GUARANTEED FORM (probe run 1 P5 + run 2:
    #    a fresh process re-registers everything generated before it). The
    #    registry, never a walk of the data dir, is the index.
    try:
        from . import skills_out
        n = skills_out.register_all(ctx)
        log.info("reapx: %s mode, %d generated skill(s) registered "
                 "(startup registry scan)", mode, n)
    except Exception:
        log.warning("reapx: startup skills scan failed", exc_info=True)

    # 5. the one bundled static skill (A.7.5): the agent always has
    #    reapx:index from install.
    bundled = Path(__file__).resolve().parent / "skills"
    try:
        for child in sorted(bundled.iterdir()) if bundled.is_dir() else []:
            if (child / "SKILL.md").is_file():
                try:
                    ctx.register_skill(child.name, child / "SKILL.md",
                                       "ReapX index: what the plugin "
                                       "generated + how to open it")
                except Exception:
                    log.warning("reapx: bundled skill %r failed", child.name,
                                exc_info=True)
    except Exception:
        log.warning("reapx: bundled skills scan failed", exc_info=True)

    # 6. the engine-module eviction (P3): the plugin layer imports the
    #    engine under its own namespace (the loader evicts that on
    #    reload), but the reapx-only fetch chain flat-imports a few
    #    reapx-exclusive top-level names — on_unload removes them so a
    #    re-load always runs fresh engine code.
    try:
        ctx.on_unload(runtime.run_unload)
    except Exception:
        log.warning("reapx: on_unload registration failed", exc_info=True)

    # 7. the cron-surface replacement for run_reapx_3day.sh (A.10 gap 8):
    #    an Automation Blueprint (keys namespaced reapx:harvest; the
    #    operator must explicitly schedule it; jobs outlive the plugin; no
    #    manifest capability needed). The prompt is non-interactive by
    #    design (R12).
    try:
        ctx.register_automation_blueprint(
            "harvest", title="ReapX harvest", category="skills",
            description="Every 3 days: harvest new X bookmarks, assess "
                        "them, generate + register skills. Non-interactive; "
                        "fails clean.",
            schedule_template="0 6 */3 * *",
            prompt_template="Run a ReapX harvest with a small scroll cap "
                            "(max_scrolls=80), then summarize what was new. "
                            "Do not ask questions; if the harvest errors, "
                            "report the error only.",
        )
    except Exception:
        log.warning("reapx: automation blueprint registration failed",
                    exc_info=True)
