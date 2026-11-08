"""_engine.py — the lazy, namespace-safe engine adapter (A.2.1.2 seam).

The one rule that shapes this module: **the engine keeps its standalone
shape** (one tree, two install shapes). The standalone runs the engine as
``python3 scripts/<x>.py`` with flat sibling imports; the plugin therefore
imports the engine the very same way — through ``scripts/`` — but under its
own package namespace (``hermes_plugins.reapx.scripts.*``), which gives:

  * zero top-level ``sys.modules`` pollution for the shared engine
    (map/categorize/assess/generate/ledger) — two plugins in one hermes never
    fight over ``generate_skills`` (ReapX's and StarLearner's differ);
  * the loader's own ``_evict_modules("hermes_plugins.reapx.*")`` evicts the
    engine on plugin reload/doctor-exit (fresh code after an update);
  * the one flat sibling a plugin-path engine module still imports
    (``reapx_paths``, from map_bookmarks_to_repos.py's own standalone-era
    idiom) is reapx-prefixed, so it cannot collide with another plugin's
    module; ``evict()`` removes it via ``runtime.on_unload``.

The browser chain (``fetch_x_bookmarks`` / ``reapx_browser_locator`` /
``reapx_cdp_launcher`` / ``cookie_store`` / ``reapx_config``) is NEVER
imported by the plugin — browser mode lives in the standalone scripts. That
is the whole point of this build: the plugin reads no browser credential
store.

Nothing hermes is imported here. ``load()`` is idempotent; every engine
failure is a clean RuntimeError for the pipeline error model (R12).
"""
from __future__ import annotations

import sys

# The shared engine, imported under the plugin's own namespace.
# (Populated by load(); module attributes are set there so that a failed
# engine import surfaces as a clean RuntimeError, not an import error at
# plugin-register time — nothing here runs before the first tool call.)
ledger = None
categorize_repos = None
assess_repos = None
generate_skills = None
map_bookmarks_to_repos = None

# Top-level module names the engine still flat-imports (its own standalone
# idiom). All reapx-prefixed: no collision with another plugin's module.
ENGINE_TOP_LEVEL = ("reapx_paths",)

_LOADED = False


def load() -> None:
    """Import the shared engine (map / categorize / assess / generate /
    ledger — none of it needs a browser or websocket-client, so the plugin
    path works on any hermes). Idempotent; raises RuntimeError (never a bare
    ImportError) when the engine cannot load.

    R09 discipline — the path-restore: the engine scripts carry their own
    standalone-era ``sys.path.insert(...)`` idiom. Under the plugin that
    would leave THE PLUGIN ROOT at ``sys.path[0]``, and every plugin root
    carries a top-level ``tools.py``/``scripts`` package — shadowing hermes
    core's own top-level ``tools``/``scripts`` packages for every later
    LAZY core import (core imports ``tools.*`` lazily all over: approval
    contexts, mcp costs, …). Everything the engine needs from the path it
    imports at import time (its flat sibling lands in sys.modules), so the
    path is restored to exactly what it was after every load.
    """
    global _LOADED, ledger, categorize_repos, assess_repos, generate_skills
    global map_bookmarks_to_repos
    if _LOADED:
        return
    saved_path = list(sys.path)
    try:
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
    finally:
        sys.path[:] = saved_path
    _register_eviction()
    _LOADED = True


def _register_eviction() -> None:
    # The map stage flat-imports reapx_paths at its own import time (lands
    # top-level — a reapx-exclusive name). Register its eviction.
    from . import runtime
    runtime.on_unload(evict)


def evict() -> None:
    """Pop the reapx-exclusive top-level engine modules (on_unload)."""
    for name in ENGINE_TOP_LEVEL:
        sys.modules.pop(name, None)
