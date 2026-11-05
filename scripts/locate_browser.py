#!/usr/bin/env python3
"""locate_browser.py — diagnostic: report OS, installed browsers, cookie paths.

Prints PATHS ONLY. Never prints or touches cookie values.
Usage:  python3 scripts/locate_browser.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_locator import (
    detect_os,
    get_installed_browsers,
    resolve_cookie_db,
)


def main():
    os_name = detect_os()
    print(f"Detected OS: {os_name}")

    installed = get_installed_browsers()
    print(f"Installed Chromium-family browsers: {installed or 'none detected'}")

    if not installed:
        print("No browsers found — nothing to resolve.")
        return

    print("\nResolvable cookie DB paths (path only, never values):")
    for browser in installed:
        path = resolve_cookie_db(browser)
        print(f"  {browser:<7} -> {path or '(cookie DB not found)'}")

    # Also show the default profile path even if the DB file is absent,
    # so a user can tell "not installed" from "not logged in yet".
    print("\nProbing 'Default' profile presence for all known browsers:")
    for browser in ["brave", "chrome", "edge", "arc", "opera"]:
        path = resolve_cookie_db(browser)
        status = "present" if path else "absent"
        print(f"  {browser:<7} : {status}")


if __name__ == "__main__":
    main()
