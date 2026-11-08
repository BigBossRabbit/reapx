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
    the 10s load deadline and the install scanner never see a pipeline;
  * no network, no subprocess, no browser, no writes outside
    ``plugin_data_dir`` — nothing here can hang an unattended start (R12);
  * every optional step (aux task, startup skill scan, blueprint, on_unload)
    is individually guarded — a failure degrades one feature (logged), never
    the load;
  * THE PROBE ANSWER (A.7.4): handler-time ``ctx.register_skill`` IS
    accepted and the skill is live in the same session
    (``skill_view("reapx:<name>")`` resolves it through the real agent path)
    — the pipeline calls it through ``runtime.get_ctx()``. The
    register()-time ``skills_out.register_all()`` scan of the plugin_db
    registry remains the guaranteed next-session form.

INPUT ONLY. The plugin never reads a browser credential store: bookmarks
arrive as a file the user points at, or as inline items the AGENT collected
through the host's own browser tool. The ``scripts/`` browser machinery
(fetch_x_bookmarks, reapx_browser_locator, reapx_cdp_launcher) is the
standalone path; nothing in this package imports it at any time.

One tree, two install shapes (A.12): the same repo keeps working as the
standalone skill-pipeline (``bash scripts/daily_sync.sh``, the Makefile
targets, ``tests/run_smoke.sh``, ``scripts/run_reapx_3day.sh`` cron) —
nothing a standalone user runs changes, breaks, or requires Hermes.
"""
from __future__ import annotations

from pathlib import Path

from . import runtime, schemas

# Toolset.
TOOLSET = "reapx"


def register(ctx) -> None:
    """The plugin's whole registration surface (see the module docstring)."""
    # 0. the captured context: handler-time ctx.register_skill (A.7.4 probe
    #    WORLD A) + settings reads run through it. Set FIRST.
    runtime.set_ctx(ctx)
    log = runtime.log

    # 1. our own auxiliary LLM task (zero consent: a plugin may only use a
    #    task it registered itself; operators pin it under
    #    auxiliary.reapx_enricher — it follows the compression slot until
    #    they do). KEY MUST BE ALNUM+UNDERSCORE (a hyphen raises ValueError
    #    and fails the whole load).
    try:
        ctx.register_auxiliary_task(
            runtime.AUX_TASK,
            display_name="ReapX Enricher",
            description="Map/categorize/assess enrichment for the ReapX "
                        "import (bookmark text → skill metadata). Follows "
                        "your active model until you pin one.",
            inherit_from="compression",
            defaults={"timeout": 60},
        )
    except Exception:  # a failed aux task degrades llm enrichment to rules
        log.warning("reapx: register_auxiliary_task(%r) failed",
                    runtime.AUX_TASK, exc_info=True)

    # 2. the three tools (A.3). Registered set == declared set ==
    #    provides_tools (R06).
    from . import tools

    ctx.register_tool(
        name="reapx_import", toolset=TOOLSET, schema=schemas.IMPORT,
        handler=tools.import_,
    )
    ctx.register_tool(
        name="reapx_status", toolset=TOOLSET, schema=schemas.STATUS,
        handler=tools.status,
    )
    ctx.register_tool(
        name="reapx_review", toolset=TOOLSET, schema=schemas.REVIEW,
        handler=tools.review,
    )

    # 3. generated-skills registration (A.7): the startup scan of the
    #    plugin_db registry — THE GUARANTEED FORM (probe run 1 P5 + run 2:
    #    a fresh process re-registers everything generated before it). The
    #    registry, never a walk of the data dir, is the index.
    try:
        from . import skills_out
        n = skills_out.register_all(ctx)
        log.info("reapx: %d generated skill(s) registered "
                 "(startup registry scan)", n)
    except Exception:
        log.warning("reapx: startup skills scan failed", exc_info=True)

    # 4. the one bundled static skill (A.7.5): the agent always has
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

    # 5. the engine-module eviction: the plugin layer imports the engine
    #    under its own namespace (the loader evicts that on reload), but the
    #    map stage flat-imports reapx_paths at top level (its own
    #    standalone-era idiom) — on_unload removes it so a re-load always
    #    runs fresh engine code.
    try:
        ctx.on_unload(runtime.run_unload)
    except Exception:
        log.warning("reapx: on_unload registration failed", exc_info=True)

    # 6. the cron-surface replacement for run_reapx_3day.sh (A.10 gap 8):
    #    an Automation Blueprint (keys namespaced reapx:harvest; the
    #    operator must explicitly schedule it; jobs outlive the plugin; no
    #    manifest capability needed). The prompt is non-interactive by
    #    design (R12) and reads the import_dir file, so a cron run needs no
    #    browser and no conversation.
    try:
        ctx.register_automation_blueprint(
            "harvest", title="ReapX import", category="skills",
            description="Every 3 days: import new X bookmarks (the file in "
                        "the import_dir setting), assess them, generate "
                        "skills. Non-interactive; fails clean.",
            schedule_template="0 6 */3 * *",
            prompt_template="Run reapx_import with no arguments (it reads "
                            "the configured import_dir file), then "
                            "summarize what was new. Do not ask questions; "
                            "if the import errors, report the error only.",
        )
    except Exception:
        log.warning("reapx: automation blueprint registration failed",
                    exc_info=True)
