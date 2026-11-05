"""_engine.py — the lazy, namespace-safe engine adapter (A.2.1.2 seam).

The one rule that shapes this module: **the engine keeps its standalone
shape** (one tree, two install shapes). The standalone runs the engine as
``python3 scripts/<x>.py`` with flat sibling imports
(``sys.path.insert(0, scripts/)`` inside fetch_x_bookmarks.py /
reapx_config.py); the plugin therefore imports the engine the very same way —
through ``scripts/`` — but under its own package namespace
(``hermes_plugins.reapx.scripts.*``), which gives:

  * zero top-level ``sys.modules`` pollution for the shared engine
    (map/categorize/assess/generate/ledger) — two plugins in one hermes never
    fight over ``generate_skills`` (ReapX's and StarLearner's differ);
  * the loader's own ``_evict_modules("hermes_plugins.reapx.*")`` evicts the
    engine on plugin reload/doctor-exit (fresh code after an update);
  * the reapx-only fetch chain (fetch_x_bookmarks → browser_locator /
    cdp_launcher / reapx_paths) still lands a few reapx-EXCLUSIVE top-level
    names (its own flat imports); ``evict()`` removes exactly those via
    ``runtime.on_unload``.

Nothing hermes is imported here. ``load()`` is idempotent; every engine
failure (e.g. the missing ``websocket-client`` import inside
fetch_x_bookmarks, which exits at import time) is converted into a clean
RuntimeError for the harvest error model (R12).
"""
from __future__ import annotations

import sys
from pathlib import Path

# The shared engine, imported under the plugin's own namespace.
# (Populated by load(); module attributes are set there so that a failed
# engine import surfaces as a clean RuntimeError, not an import error at
# plugin-register time — nothing here runs before the first tool call.)
ledger = None
categorize_repos = None
assess_repos = None
generate_skills = None
map_bookmarks_to_repos = None
fetch_x_bookmarks = None

# Top-level module names the reapx-only fetch chain flat-imports (its own
# standalone idiom). The shared engine never lands here (namespace imports);
# these are evicted on plugin unload via runtime.on_unload.
ENGINE_TOP_LEVEL = ("browser_locator", "cdp_launcher", "reapx_paths")

_LOADED = False
_FETCH_LOADED = False


def load() -> None:
    """Import the shared engine (map / categorize / assess / generate /
    ledger — none of it needs a browser or websocket-client, so the EXPORT
    mode path works on any hermes). Idempotent; raises RuntimeError (never a
    bare ImportError) when the engine cannot load."""
    global _LOADED, ledger, categorize_repos, assess_repos, generate_skills
    global map_bookmarks_to_repos
    if _LOADED:
        return
    try:
        _boot_path()
        # Package-relative: hermes_plugins.reapx.scripts.<name> — the plugin's
        # own copy of the engine, evictable by the plugin loader, invisible
        # to any other plugin's same-named engine modules.
        from . import scripts  # noqa: F401  (hermes_plugins.reapx.scripts)
        from .scripts import (ledger, categorize_repos, assess_repos,  # type: ignore # noqa: F401
                              generate_skills, map_bookmarks_to_repos)  # type: ignore # noqa: F401
    except ImportError as exc:
        raise RuntimeError(f"engine import failed: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"engine load failed: {type(exc).__name__}: "
                           f"{exc}") from exc
    _register_eviction()
    _LOADED = True


def load_fetch() -> None:
    """Import the fetch chain (BROWSER mode only): fetch_x_bookmarks +
    its flat siblings (browser_locator / cdp_launcher). Its guarded
    ``websocket-client`` import (a sys.exit at import time) is converted
    into a clean RuntimeError for the harvest error model (R12) — the
    export path never calls this, so a missing websocket can only ever
    produce reapx_harvest's ``dependency_missing`` error."""
    global _FETCH_LOADED, fetch_x_bookmarks
    if _FETCH_LOADED:
        return
    load()
    try:
        from .scripts import fetch_x_bookmarks  # type: ignore # noqa: F401
    except SystemExit as exc:  # the engine's guarded websocket import
        raise RuntimeError(str(exc) or "engine dependency missing "
                                       "(websocket-client)") from exc
    except ImportError as exc:
        raise RuntimeError(f"engine import failed: {exc}") from exc
    _FETCH_LOADED = True


def _boot_path() -> None:
    """The engine's own flat-import idiom (what fetch_x_bookmarks.py does
    for its siblings): scripts/ on sys.path. Idempotent."""
    scripts_dir = Path(__file__).resolve().parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


def _register_eviction() -> None:
    # The reapx-only fetch chain flat-imports its siblings at its own import
    # time (browser_locator / cdp_launcher / reapx_paths land top-level —
    # reapx-exclusive names). Register their eviction.
    from . import runtime
    runtime.on_unload(evict)


def evict() -> None:
    """Pop the reapx-exclusive top-level engine modules (on_unload)."""
    for name in ENGINE_TOP_LEVEL:
        sys.modules.pop(name, None)
