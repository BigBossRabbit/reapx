#!/usr/bin/env python3
"""reapx_config.py — config-driven settings for ReapX v2.

Parses CLI flags (or environment variables) and auto-resolves the browser via
browser_locator when --browser=auto.

CLI precedence over env:
    --browser  | REAPX_BROWSER  (default 'auto')
    --profile  | REAPX_PROFILE  (default 'Default')
    --port     | REAPX_PORT     (default 0 = auto-free)
    --output   | REAPX_OUTPUT   (default data/x_bookmarks.json)

Cookie VALUES are never handled here; this module only resolves config/paths.
"""
import argparse
import os
import sys
from pathlib import Path

from browser_locator import get_installed_browsers

DEFAULT_OUTPUT = Path("data/x_bookmarks.json")


def _env(name, default):
    val = os.environ.get(name)
    return val if val is not None and val != "" else default


def build_parser():
    p = argparse.ArgumentParser(
        prog="reapx",
        description="ReapX v2 — cross-browser, cross-platform X bookmark fetcher.")
    p.add_argument("--browser", default=None,
                   help="brave|chrome|edge|arc|opera|auto (default: auto)")
    p.add_argument("--profile", default=None,
                   help="profile directory name (default: Default)")
    p.add_argument("--port", default=None, type=int,
                   help="CDP port; 0 = auto-free (default: 0)")
    p.add_argument("--output", default=None,
                   help="output JSON path (default: data/x_bookmarks.json)")
    p.add_argument("--keep-profile", action="store_true",
                   help="keep the temp CDP profile dir after run (debugging)")
    p.add_argument("--max-scrolls", default=None, type=int,
                   help="upper bound on infinite-load scroll iterations "
                        "(default: 500)")
    return p


def auto_resolve_browser():
    """Return the first installed browser name, or 'brave' as a safe fallback."""
    installed = get_installed_browsers()
    if installed:
        return installed[0]
    # No Chromium-family browser detected — default to brave and let the
    # downstream call surface the real error.
    return "brave"


def load_config(argv=None):
    """Parse argv/env and return a resolved namespace.

    Returns a Namespace with: browser, profile, port, output (a Path),
    and raw args for debugging.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    browser = args.browser or _env("REAPX_BROWSER", "auto")
    profile = args.profile or _env("REAPX_PROFILE", "Default")
    port = args.port if args.port is not None else int(_env("REAPX_PORT", "0"))
    output = Path(args.output or _env("REAPX_OUTPUT", str(DEFAULT_OUTPUT)))

    if browser == "auto":
        browser = auto_resolve_browser()

    max_scrolls = args.max_scrolls if args.max_scrolls is not None \
        else int(_env("REAPX_MAX_SCROLLS", "500"))

    return argparse.Namespace(
        browser=browser,
        profile=profile,
        port=port,
        output=output,
        keep_profile=bool(args.keep_profile),
        max_scrolls=max_scrolls,
        raw=args,
    )


if __name__ == "__main__":
    cfg = load_config()
    print(f"browser : {cfg.browser}")
    print(f"profile : {cfg.profile}")
    print(f"port    : {cfg.port}")
    print(f"output  : {cfg.output}")
