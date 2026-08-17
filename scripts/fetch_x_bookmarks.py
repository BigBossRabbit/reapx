#!/usr/bin/env python3
"""fetch_x_bookmarks.py — production fetcher for the user's X (Twitter) bookmarks.

Architecture (no paid X API):
  1. Resolve the browser + profile from config (reapx_config), then copy that
     browser's live 'Cookies' SQLite file into a fresh temp profile
     (READ-ONLY on the source; non-destructive). This carries the authenticated
     x.com session natively — X accepts it exactly as the real browser would.
  2. Locate the browser binary + launch headless over CDP (cdp_launcher) on a
     FREE port (never the hardcoded 9222) against that copied profile.
  3. Navigate to https://x.com/i/history, then repeatedly scroll to the bottom
     to trigger X's infinite load, collecting every <article> bookmark.
  4. Extract per tweet: id, full text, author screen_name, created_at, URLs.
  5. Write the configured output JSON (idempotent, deduped by id, atomic write).

Fully config-driven + cross-browser + cross-platform:
    --browser brave|chrome|edge|arc|opera|auto   (auto = first installed)
    --profile <profile dir name>                 (default 'Default')
    --port <int>                                  (0 = auto-free)
    --output <path>
    --keep-profile                                (keep temp CDP profile; debug)
    --max-scrolls <int>                           (default 500)

Cookie VALUES are never printed, logged, or persisted. The source cookie DB is
opened read-only (a copy is made; the original is never touched). The session
verification contract (auth_token, ct0, _twitter_sess) is preserved.
"""
import json
import os
import shutil
import sys
import time
import tempfile
import urllib.request
from pathlib import Path

try:
    import websocket  # websocket-client
except ImportError:
    sys.exit("ERROR: 'websocket-client' is required. Run: pip3 install websocket-client")

# v2 portability layer.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_locator import resolve_cookie_db, get_installed_browsers
import cdp_launcher
from reapx_config import load_config, auto_resolve_browser

# ----------------------------------------------------------------------------
# Runtime constants (behavior; browser/OS specifics are config-driven).
# ----------------------------------------------------------------------------
X_HISTORY_URL = "https://x.com/i/history"
SCROLL_WAIT = 1.8         # seconds between scrolls (rate-limit politely)
CONSECUTIVE_EXHAUST = 10  # consecutive iterations with no scrollHeight growth => end

# ----------------------------------------------------------------------------
# Temp profile + headless CDP launch (v2: cdp_launcher, no pkill / no 9222)
# ----------------------------------------------------------------------------
def prepare_profile(cookie_db):
    """Copy a browser's live Cookies DB into a fresh temp user-data-dir.

    Returns the temp profile dir path. The source Cookies file is opened with
    copy2 (read-only on source, non-destructive); the temp dir is removed by
    cdp_launcher.cleanup unless --keep-profile is set.
    """
    tmp = tempfile.mkdtemp(prefix="reapx_cdp_profile_")
    default_dir = os.path.join(tmp, "Default")
    os.makedirs(default_dir, exist_ok=True)
    if not cookie_db or not os.path.exists(cookie_db):
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Source cookie DB not found: {cookie_db}")
    shutil.copy2(cookie_db, os.path.join(default_dir, "Cookies"))
    return tmp


# ----------------------------------------------------------------------------
# Headless CDP (identical client to v1)
# ----------------------------------------------------------------------------
class CDP:
    """Minimal CDP client over a single websocket connection."""

    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=40)
        self.ws.settimeout(40)
        self.id = 0

    def send(self, method, params=None):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg
            if msg.get("method") in (
                "Network.requestWillBeSent", "Network.responseReceived",
                "Network.loadingFinished", "Network.requestWillBeSentExtraInfo",
                "Network.responseReceivedExtraInfo"):
                continue

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def js(page, expression):
    """Evaluate JS in the page and return the deserialized value."""
    res = page.send("Runtime.evaluate",
                    {"expression": expression, "returnByValue": True,
                     "awaitPromise": True})
    if res.get("result", {}).get("exceptionDetails"):
        raise RuntimeError("JS error: " +
                           json.dumps(res["result"]["exceptionDetails"])[:400])
    return res.get("result", {}).get("result", {}).get("value")


def wait_for_cdp(port, timeout=30):
    """Poll the CDP HTTP endpoint until it answers; return the /json/version dict.

    Tries IPv6 then IPv4 (browsers may bind only one). Raises RuntimeError on
    timeout.
    """
    for _ in range(int(timeout / 0.5)):
        for base in ("http://[::1]:%d" % port, "http://127.0.0.1:%d" % port):
            try:
                with urllib.request.urlopen(base + "/json/version", timeout=2) as r:
                    return json.loads(r.read())
            except Exception:
                continue
        time.sleep(0.5)
    raise RuntimeError("CDP endpoint never came up on port %d" % port)


def new_page(browser, port):
    """Create a page target and return its CDP client."""
    tgt = browser.send("Target.createTarget", {"url": "about:blank"})["result"]["targetId"]
    page_ws = None
    for _ in range(20):
        for base in ("http://[::1]:%d" % port, "http://127.0.0.1:%d" % port):
            try:
                with urllib.request.urlopen(base + "/json", timeout=3) as r:
                    for t in json.loads(r.read()):
                        if t.get("id") == tgt:
                            page_ws = t["webSocketDebuggerUrl"]
                            break
            except Exception:
                continue
            if page_ws:
                break
        if page_ws:
            break
        time.sleep(0.3)
    if not page_ws:
        raise RuntimeError("Could not resolve page websocket for new target")
    page = CDP(page_ws)
    page.send("Network.enable")
    page.send("Page.enable")
    return page


# ----------------------------------------------------------------------------
# Extraction (identical to v1 — do NOT regress the 128-bookmark harvest)
# ----------------------------------------------------------------------------
EXTRACT_JS = r"""
(() => {
  const out = [];
  document.querySelectorAll('article').forEach((art) => {
    const link = art.querySelector('a[href*="/status/"]');
    const idMatch = link && link.href.match(/\/status\/(\d+)/);
    if (!idMatch) return;
    const id = idMatch[1];
    let author = null;
    const authorLink = art.querySelector('a[href^="/"]');
    if (authorLink) {
      const m = authorLink.getAttribute('href').match(/^\/([^\/]+)$/);
      if (m) author = m[1];
    }
    const textEl = art.querySelector('[data-testid="tweetText"]');
    const text = textEl ? textEl.innerText : '';
    const timeEl = art.querySelector('time');
    const created_at = timeEl ? (timeEl.getAttribute('datetime') || null) : null;
    const urls = Array.from(art.querySelectorAll('a[href]'))
      .map(a => a.href)
      .filter(h => h.startsWith('http') && !h.includes('x.com') && !h.includes('twitter.com'));
    out.push({id, author, text, created_at, urls: [...new Set(urls)]});
  });
  return out;
})()
"""


def extract_articles(page):
    return js(page, EXTRACT_JS)


# Scroll to the bottom of X's actual scroll container and return its metrics.
# X virtualizes the timeline (only ~10-15 <article> in the DOM), so exhaustion
# must be detected by scroll POSITION / scrollHeight growth, not article count.
# `document.scrollingElement` is used when it scrolls; otherwise the tallest
# scrollable element is found (X uses a dedicated scroll container).
SCROLL_JS = r"""
(() => {
  const se = document.scrollingElement;
  let el = null;
  if (se && se.scrollHeight > se.clientHeight) {
    el = se;
  } else {
    let best = null, bestScore = 0;
    document.querySelectorAll('*').forEach((e) => {
      if (e.scrollHeight > e.clientHeight + 50 && e.scrollHeight > bestScore) {
        bestScore = e.scrollHeight;
        best = e;
      }
    });
    el = best;
  }
  if (el) el.scrollTop = el.scrollHeight;   // jump to the bottom of the container
  window.scrollTo(0, document.body.scrollHeight); // native fallback
  let scrollTop = 0, scrollHeight = 0, clientHeight = 0;
  if (el) {
    scrollTop = el.scrollTop;
    scrollHeight = el.scrollHeight;
    clientHeight = el.clientHeight;
  } else {
    scrollTop = window.scrollY || document.documentElement.scrollTop || 0;
    scrollHeight = document.body ? document.body.scrollHeight : 0;
    clientHeight = window.innerHeight || 0;
  }
  // Cheap sentinel check: walk text nodes, stop at first match (no full innerText).
  let caught_up = false;
  if (document.body) {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        return /(all caught up|no more (posts|bookmarks)|end of)/i.test(n.textContent || '')
          ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });
    caught_up = !!walker.nextNode();
  }
  return {
    found: !!el,
    scrollTop: scrollTop,
    scrollHeight: scrollHeight,
    clientHeight: clientHeight,
    at_bottom: (scrollHeight - clientHeight - scrollTop) <= 2,
    caught_up: caught_up
  };
})()
"""


def scroll_to_end(page):
    """Scroll to the bottom of X's scroll container; return its metrics dict."""
    return js(page, SCROLL_JS)


def main(argv=None):
    cfg = load_config(argv)
    browser = cfg.browser
    profile = cfg.profile
    output = Path(cfg.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    # 1. Resolve cookie DB via the locator (browser-agnostic).
    cookie_db = resolve_cookie_db(browser, profile)
    if not cookie_db:
        raise RuntimeError(
            f"Cookie DB not found for browser='{browser}' profile='{profile}'. "
            f"Installed browsers: {get_installed_browsers() or 'none'}")

    # 2. (No Keychain check here — by design.) The fetch below runs the copied
    #    cookie DB through a headless browser over CDP, which authenticates
    #    exactly like a real browser and detects a dead session via the
    #    login-redirect check after navigation. A separate Keychain decryption
    #    pre-check would add a macOS-only dependency that can hang on an
    #    authorization prompt (cron/CI/fresh machines) for zero benefit.
    #    Use scripts/verify_x_bookmarks.py for an explicit decryptability test.

    # 3. Copy live Cookies into a temp profile + launch headless on a free port.
    profile_dir = prepare_profile(cookie_db)
    port = cfg.port if cfg.port else cdp_launcher.find_free_port()
    proc, _ = cdp_launcher.launch(browser, profile_dir, port)
    browser_cdp = None
    page = None
    try:
        version = wait_for_cdp(port)
        browser_cdp = CDP(version["webSocketDebuggerUrl"])
        page = new_page(browser_cdp, port)

        # 4. Navigate to bookmarks.
        page.send("Page.navigate", {"url": X_HISTORY_URL})
        time.sleep(12)
        title = js(page, "document.title")
        href = js(page, "location.href")
        print(f"[fetch_x_bookmarks] Loaded: '{title}' @ {href}")
        if "onboarding" in (href or "") or "login" in (href or ""):
            raise RuntimeError("Redirected to login — copied session not accepted")

        # 5. Infinite scroll, ACCUMULATING every bookmark that passes through
        #    the viewport. X virtualizes the timeline: only ~10-15 <article> are
        #    in the DOM at once and older ones are destroyed as you scroll, so
        #    we merge each iteration's DOM snapshot into a persistent dict keyed
        #    by id. True exhaustion is detected by scroll POSITION (scrollHeight
        #    stops growing while pinned to the bottom), NOT by article count.
        collected = {}          # id -> bookmark (dedupe by id across scrolls)
        prev_scroll_height = 0
        no_gain = 0             # consecutive iterations with no scrollHeight growth
        max_scrolls = int(cfg.max_scrolls or 500)
        for i in range(max_scrolls):
            st = scroll_to_end(page)
            time.sleep(SCROLL_WAIT)

            # Merge whatever is currently in the DOM into the persistent store.
            batch = extract_articles(page) or []
            new = 0
            for b in batch:
                if b.get("id") not in collected:
                    collected[b["id"]] = b
                    new += 1

            sh = int(st.get("scrollHeight") or 0)
            at_bottom = bool(st.get("at_bottom"))
            print(f"[fetch_x_bookmarks] scroll {i+1}: +{new} new (total "
                  f"{len(collected)}) [scrollH={sh}, at_bottom={at_bottom}]")

            # Exhaustion signals.
            if st.get("caught_up"):
                print("[fetch_x_bookmarks] 'All caught up' sentinel -> done")
                break
            if sh == prev_scroll_height and sh > 0:
                no_gain += 1
                if no_gain >= CONSECUTIVE_EXHAUST:
                    print(f"[fetch_x_bookmarks] scrollHeight stable for "
                          f"{CONSECUTIVE_EXHAUST} iterations -> reached end")
                    break
            else:
                no_gain = 0
            prev_scroll_height = sh

        result = sorted(collected.values(), key=lambda x: int(x["id"]), reverse=True)

        # 6. Atomic write (resumable/idempotent).
        tmp = output.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        os.replace(tmp, output)
        print(f"[fetch_x_bookmarks] Wrote {len(result)} unique bookmarks -> {output}")
    finally:
        if page is not None:
            page.close()
        if browser_cdp is not None:
            browser_cdp.close()
        cdp_launcher.cleanup(proc, profile_dir, keep=cfg.keep_profile)


if __name__ == "__main__":
    main()
