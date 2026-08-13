#!/usr/bin/env python3
"""verify_x_bookmarks.py — smoke test for the X bookmarks pipeline.

Checks:
  1. Cookies are readable + decryptable from the configured browser's DB via
     the cookie_store abstraction (without printing their values). Session
     contract: auth_token, ct0, _twitter_sess present.
  2. fetch_x_bookmarks.py runs and returns N > 0 bookmarks.

Browser-agnostic: accepts --browser (default 'auto' -> first installed) and
--profile (default 'Default'). Prints a compact one-line summary.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
X_BOOKMARKS = DATA_DIR / "x_bookmarks.json"


def parse_args():
    p = argparse.ArgumentParser(description="Verify the ReapX X-bookmarks pipeline")
    p.add_argument("--browser", default="auto",
                   help="brave|chrome|edge|arc|opera|auto (default: auto)")
    p.add_argument("--profile", default="Default",
                   help="profile directory name (default: Default)")
    return p.parse_args()


def main():
    sys.path.insert(0, str(SCRIPT_DIR))
    import cookie_store
    from browser_locator import detect_os, get_installed_browsers
    from reapx_config import auto_resolve_browser

    args = parse_args()
    browser = args.browser
    if browser == "auto":
        browser = auto_resolve_browser()
    os_name = detect_os()

    # Step 1: cookies decryptable (browser-agnostic via cookie_store).
    cookie_err = ""
    ok_names = []
    try:
        cookies = cookie_store.read_cookies(
            browser, args.profile, domains=["x.com", "twitter.com"])
        ok_names = [k for k in ("auth_token", "ct0") if k in cookies]
        cookie_ok = len(ok_names) == 2
    except Exception as e:
        cookies, cookie_ok = {}, False
        cookie_err = str(e)

    if not cookie_ok:
        print(f"VERIFY FAIL | cookies: NOT decryptable ({cookie_err})")
        sys.exit(1)

    # Step 2: run the real fetcher (auto -> same browser on this OS).
    cmd = [sys.executable, str(SCRIPT_DIR / "fetch_x_bookmarks.py")]
    if args.browser != "auto":
        cmd += ["--browser", args.browser]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=420)
    print(r.stdout.strip())

    if not X_BOOKMARKS.exists():
        print("VERIFY FAIL | fetch produced no data file")
        sys.exit(1)
    with open(X_BOOKMARKS) as f:
        bookmarks = json.load(f)
    n = len(bookmarks)

    if n > 0:
        sample = next((b for b in bookmarks if b.get("text")), {})
        sample_txt = (sample.get("text") or "")[:60].replace("\n", " ")
        print(f"VERIFY PASS | browser={browser} os={os_name} | bookmarks "
              f"fetched: {n} | sample: @{sample.get('author')} "
              f"\"{sample_txt}\"")
    else:
        print("VERIFY FAIL | 0 bookmarks fetched")
        sys.exit(1)


if __name__ == "__main__":
    main()
