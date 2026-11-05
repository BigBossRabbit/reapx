#!/usr/bin/env python3
"""probe_followers_page.py — READ-ONLY diagnosis of why /followers serves few cells.

Reuses fetch_x_graph's proven CDP + cookie-copy machinery. Reports, per list:
  - document.title (X embeds the real count, e.g. "(6) People following")
  - UserCell count at 8/15/25/40s, split by primary column vs sidebar
  - rate-limit / error / empty-state interstitial markers
  - the actual handles rendered
Never prints cookie or token values.
"""
import os
import sys
import time
import shutil
import tempfile
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from browser_locator import resolve_cookie_db          # noqa: E402
from cdp_launcher import launch, find_free_port, cleanup  # noqa: E402
from fetch_x_graph import CDP, new_page, wait_for_cdp, wait_for_element, js  # noqa: E402

PROBE_JS = r"""
(() => {
  const out = {};
  out.title = document.title;
  out.url = location.href;

  const cells = document.querySelectorAll('[data-testid="UserCell"]');
  out.cellTotal = cells.length;

  const primary = document.querySelector('[data-testid="primaryColumn"]');
  const sidebar = document.querySelector('[data-testid="sidebarColumn"]');
  out.inPrimary = primary ? primary.querySelectorAll('[data-testid="UserCell"]').length : -1;
  out.inSidebar = sidebar ? sidebar.querySelectorAll('[data-testid="UserCell"]').length : -1;

  const tl = document.querySelector('[aria-label*="Timeline"]');
  out.timelineLabel = tl ? tl.getAttribute('aria-label') : null;
  out.timelineCells = tl ? tl.querySelectorAll('[data-testid="UserCell"]').length : -1;

  const bodyText = document.body ? (document.body.innerText || '') : '';
  const markers = ['Something went wrong', 'Try again', 'Retry', 'rate limit',
                   'Rate limit', 'These accounts are protected', "any followers",
                   'Log in', 'Sign in', 'Caught up', 'suspended', 'restricted'];
  out.markers = markers.filter(m => bodyText.includes(m));

  out.retryButtons = Array.from(document.querySelectorAll('div[role="button"],button'))
    .map(b => (b.innerText || '').trim())
    .filter(t => /retry|try again/i.test(t)).slice(0, 5);

  out.primaryText = primary ? (primary.innerText || '').slice(0, 500) : null;
  out.scrollHeight = document.documentElement.scrollHeight;

  out.handles = Array.from(cells).map(c => {
    const av = c.querySelector('[data-testid^="UserAvatar-Container-"]');
    return av ? av.getAttribute('data-testid').replace('UserAvatar-Container-', '') : null;
  });
  return out;
})()
"""


def probe(page, user, path, label):
    url = f"https://x.com/{user}{path}"
    print(f"\n{'=' * 66}\n{label}   {url}\n{'=' * 66}")
    page.send("Page.navigate", {"url": url})
    elapsed = 0
    info = {}
    for target in (8, 15, 25, 40):
        time.sleep(target - elapsed)
        elapsed = target
        try:
            info = js(page, PROBE_JS) or {}
        except Exception as e:
            print(f"  t={target:>2}s  probe error: {e}")
            continue
        print(f"  t={target:>2}s  cells={info.get('cellTotal'):>4}"
              f"  primary={info.get('inPrimary'):>4}"
              f"  sidebar={info.get('inSidebar'):>3}"
              f"  timeline={info.get('timelineCells'):>4}"
              f"  scrollH={info.get('scrollHeight')}")
    print(f"\n  TITLE           : {info.get('title')}")
    print(f"  timeline label  : {info.get('timelineLabel')}")
    print(f"  markers found   : {info.get('markers')}")
    print(f"  retry buttons   : {info.get('retryButtons')}")
    print(f"  handles rendered: {info.get('handles')}")
    print("  --- primary column text (first 500 chars) ---")
    print("  " + (info.get('primaryText') or '').replace("\n", "\n  ")[:500])
    return info


def main():
    user = sys.argv[1] if len(sys.argv) > 1 else "OKIN_17"
    browser = "brave"
    cookie_db = resolve_cookie_db(browser, "Default")
    if not cookie_db:
        raise RuntimeError("cookie DB not found")
    tmp = tempfile.mkdtemp(prefix="reapx_probe_")
    d = os.path.join(tmp, "Default")
    os.makedirs(d, exist_ok=True)
    shutil.copy2(cookie_db, os.path.join(d, "Cookies"))

    port = find_free_port()
    proc, _ = launch(browser, tmp, port)
    print(f"[probe] CDP port {port}")
    browser_cdp = None
    try:
        version = wait_for_cdp(port)
        browser_cdp = CDP(version["webSocketDebuggerUrl"])
        page = new_page(browser_cdp, port)
        page.send("Page.navigate", {"url": "https://x.com/home"})
        wait_for_element(page, '[data-testid="AppTabBar_Profile_Link"]', ceiling=30)
        print(f"[probe] authenticated as expected; title={js(page, 'document.title')!r}")

        probe(page, user, "/followers", "FOLLOWERS")
        probe(page, user, "/following", "FOLLOWING (control — known good)")
    finally:
        try:
            if browser_cdp:
                browser_cdp.ws.close()
        except Exception:
            pass
        cleanup(proc, tmp)
    print("\n[probe] done — read-only, nothing written")


if __name__ == "__main__":
    main()
