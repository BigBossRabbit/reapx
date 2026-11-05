#!/usr/bin/env python3
"""fetch_x_graph.py — extract followers + following from an X (Twitter) profile.

Uses the same CDP + cookie-copy architecture as ReapX:
  1. Copy Brave's live Cookies DB into a temp profile (read-only on source).
  2. Launch headless Brave over CDP on a free port.
  3. Navigate to the user's profile, then to /followers and /following.
  4. Poll for hydration (UserCell count >= 5, ceiling ~45s) — NO blind sleeps.
  5. Scroll to load all users, extracting handle, display name, bio, follows_you.
  6. Save to JSON with meta block.

DOM facts (pinned from scripts/dump_x_dom.py, not theories):
  - UserCells hydrate asynchronously: 0 at 10s, ~79 at ~20s. Must poll.
  - Display-name span has dir=null (not dir="auto"). Parse innerText structurally.
  - Handle is deterministic: data-testid="UserAvatar-Container-<handle>".
  - Cell innerText structure: [name, @handle, optional "Follows you", button, bio].
  - Follower counts / location are NOT in the list view — emit null, never fabricate.

Usage:
    python3 fetch_x_graph.py --output data/x_graph_clean.json
    python3 fetch_x_graph.py --username yourhandle --output data/x_graph_clean.json
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
    import websocket
except ImportError:
    sys.exit("ERROR: 'websocket-client' required. Run: pip3 install websocket-client")

sys.path.insert(0, '/Users/fromthejump/reapx/scripts')
from browser_locator import resolve_cookie_db, get_installed_browsers
from cdp_launcher import launch, find_free_port, cleanup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCROLL_WAIT = 1.5
CONSECUTIVE_EXHAUST = 8
MAX_SCROLLS = 1000
HYDRATION_POLL_CEILING = 45  # seconds — hard ceiling for UserCell poll
HYDRATION_MIN_CELLS = 5      # consider hydrated once we see >= this many cells

# Non-bio tokens to strip from the post-handle lines when assembling bio.
BIO_STRIP_TOKENS = {
    "Follows you", "Follow", "Follow back", "Following",
    "Unfollow", "Pending",
}

# ---------------------------------------------------------------------------
# CDP client (same as ReapX)
# ---------------------------------------------------------------------------
class CDP:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.ws = websocket.create_connection(ws_url, timeout=40)
        self.ws.settimeout(40)
        self.id = 0

    def reconnect(self):
        """Reconnect websocket after a connection drop."""
        try:
            self.ws.close()
        except Exception:
            pass
        self.ws = websocket.create_connection(self.ws_url, timeout=40)
        self.ws.settimeout(40)

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


def js(page, expression, retries=2):
    for attempt in range(retries + 1):
        try:
            res = page.send("Runtime.evaluate",
                            {"expression": expression, "returnByValue": True,
                             "awaitPromise": True})
            if res.get("result", {}).get("exceptionDetails"):
                raise RuntimeError("JS error: " +
                                   json.dumps(res["result"]["exceptionDetails"])[:400])
            return res.get("result", {}).get("result", {}).get("value")
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            if attempt < retries:
                print(f"  [js] connection dropped ({e}); reconnecting (attempt {attempt+1})")
                page.reconnect()
                continue
            raise


def wait_for_cdp(port, timeout=30):
    for _ in range(int(timeout / 0.5)):
        for base in (f"http://[::1]:{port}", f"http://127.0.0.1:{port}"):
            try:
                with urllib.request.urlopen(base + "/json/version", timeout=2) as r:
                    return json.loads(r.read())
            except Exception:
                continue
        time.sleep(0.5)
    raise RuntimeError(f"CDP endpoint never came up on port {port}")


def new_page(browser, port):
    tgt = browser.send("Target.createTarget", {"url": "about:blank"})["result"]["targetId"]
    page_ws = None
    for _ in range(20):
        for base in (f"http://[::1]:{port}", f"http://127.0.0.1:{port}"):
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


# ---------------------------------------------------------------------------
# Hydration + element waits
# ---------------------------------------------------------------------------
def wait_for_list_hydration(page, expected_path, min_cells=HYDRATION_MIN_CELLS, ceiling=HYDRATION_POLL_CEILING):
    """Poll until UserCell count >= min_cells or ceiling seconds elapse.

    Handles route transitions robustly:
    1. Wait for URL to match expected_path (navigation settled)
    2. Wait for old cells to be torn down (count drops to 0)
    3. Poll for new cells to appear (>= min_cells), printing diagnostics at 5s intervals
    4. Detect error interstitials ("Something went wrong", rate limit)

    X's follower/following lists hydrate asynchronously — at 10s the page
    may still show the noscript shell with 0 cells; at ~20s there are ~79.
    A blind sleep(8) scrolls a near-empty DOM. This polls instead.
    """
    start = time.time()

    # Step 1: Wait for URL to match the target route (navigation settled)
    url_matched = False
    while time.time() - start < 15:
        href = js(page, "location.href") or ""
        if expected_path in href:
            url_matched = True
            break
        time.sleep(0.5)

    if not url_matched:
        print(f"  [hydration] WARNING: URL never matched '{expected_path}'")

    # Step 2: Wait for old cells to be torn down (count drops to 0)
    # This prevents leftover cells from the previous page from satisfying the poll.
    tear_down_deadline = min(time.time() + 10, start + ceiling)
    while time.time() < tear_down_deadline:
        count = js(page, """
            document.querySelectorAll(
                '[data-testid="primaryColumn"] [data-testid="UserCell"]'
            ).length
        """) or 0
        if count == 0:
            break
        time.sleep(0.5)

    # Step 3: Poll for new cells to appear, with diagnostics at 5s intervals
    poll_start = time.time()
    last_print = 0
    while time.time() - start < ceiling:
        count = js(page, """
            document.querySelectorAll(
                '[data-testid="primaryColumn"] [data-testid="UserCell"]'
            ).length
        """) or 0

        # Diagnostic print at 5s intervals
        elapsed = time.time() - poll_start
        if elapsed - last_print >= 5:
            print(f"  [hydration] {elapsed:.0f}s: {count} UserCells")
            last_print = elapsed

        if count >= min_cells:
            return count

        # Step 4: Detect error interstitials
        error = js(page, """
        (() => {
            const text = (document.body && document.body.innerText) || '';
            if (/something went wrong/i.test(text)) return 'Something went wrong';
            if (/rate limit/i.test(text)) return 'Rate limited';
            if (/try again later/i.test(text)) return 'Try again later';
            return null;
        })()
        """)
        if error:
            print(f"  [hydration] DETECTED ERROR: {error}")
            return count

        time.sleep(1)

    return count


def wait_for_element(page, selector, ceiling=30):
    """Poll until document.querySelector(selector) is truthy or ceiling elapses."""
    start = time.time()
    while True:
        found = js(page, f"!!document.querySelector('{selector}')")
        if found:
            return True
        if time.time() - start >= ceiling:
            return False
        time.sleep(1)


def click_followers_tab(page):
    """X /followers renders the 'Verified Followers' tab by default.

    Wait for the tab strip to exist, read the timeline aria-label, then click
    the tab whose text is exactly 'Followers' (NOT 'Verified Followers', NOT
    'Following'). Wait until primaryColumn UserCell count exceeds the
    verified-list ceiling (>10), then return diagnostic info.

    Returns dict with: timeline_label, tab_clicked, cell_count.
    """
    # CRITICAL: the page must actually render before we look for tabs.
    # Navigating and immediately querying returns a blank document, which
    # silently leaves us on the default (Verified Followers) tab.
    start = time.time()
    tabs_seen = []
    while time.time() - start < 45:
        try:
            tabs_seen = js(page, """
            (() => {
                const scope = document.querySelector('[data-testid="primaryColumn"]');
                if (!scope) return [];
                return Array.from(scope.querySelectorAll('[role="tab"], nav a'))
                    .map(e => (e.innerText || '').trim())
                    .filter(t => t.length > 0);
            })()
            """) or []
        except Exception:
            tabs_seen = []
        if tabs_seen:
            break
        time.sleep(1)
    print(f"  [tab] tab strip after {time.time() - start:.1f}s: {tabs_seen}")

    # Read timeline label BEFORE clicking
    timeline_label = js(page, """
    (() => {
        const el = document.querySelector('[aria-label*="Timeline"]');
        return el ? el.getAttribute('aria-label') : null;
    })()
    """)
    print(f"  [tab] timeline label before click: {timeline_label!r}")

    # Find and click the exact 'Followers' tab
    tab_clicked = js(page, """
    (() => {
        // Prefer real tab semantics inside the primary column, so we click the
        // actual tab control and not some outer wrapper div that happens to
        // have the same innerText.
        const scope = document.querySelector('[data-testid="primaryColumn"]') || document;
        const cands = Array.from(scope.querySelectorAll(
            '[role="tab"], a[role="tab"], nav a, [role="tablist"] a, [role="tablist"] div'
        ));
        for (const el of cands) {
            const t = (el.innerText || '').trim();
            if (t === 'Followers') {
                // click the deepest clickable node for reliability
                const inner = el.querySelector('span') || el;
                (el.click ? el : inner).click();
                return 'Followers';
            }
        }
        // Fallback: leaf-node scan (no children) to avoid wrapper divs.
        const leaves = Array.from(scope.querySelectorAll('*')).filter(
            e => e.children.length === 0 && (e.innerText || '').trim() === 'Followers'
        );
        for (const leaf of leaves) {
            let el = leaf;
            for (let i = 0; i < 6 && el; i++) {
                if (el.getAttribute && (el.getAttribute('role') === 'tab' || el.tagName === 'A')) {
                    el.click();
                    return 'Followers';
                }
                el = el.parentElement;
            }
            leaf.click();
            return 'Followers';
        }
        return null;
    })()
    """)
    print(f"  [tab] clicked tab: {tab_clicked!r}")

    if tab_clicked != 'Followers':
        # Fallback diagnostic: what tab labels actually exist
        found_labels = js(page, """
        (() => {
            const texts = new Set();
            const all = document.querySelectorAll('*');
            for (const el of all) {
                const t = (el.innerText || '').trim();
                if (t && el.children.length === 0) texts.add(t);
            }
            return [...texts].filter(t =>
                /follower|following|verified/i.test(t)
            );
        })()
        """)
        print(f"  [tab] WARNING: could not click 'Followers' tab. "
              f"Found tab-like labels: {found_labels}")
        cell_count = js(page, """
            document.querySelectorAll(
                '[data-testid="primaryColumn"] [data-testid="UserCell"]'
            ).length
        """) or 0
        return {"timeline_label": timeline_label, "tab_clicked": tab_clicked,
                "cell_count": cell_count}

    # Wait for the full follower list to load (>10 cells in primaryColumn)
    start = time.time()
    cell_count = 0
    while time.time() - start < 45:
        cell_count = js(page, """
            document.querySelectorAll(
                '[data-testid="primaryColumn"] [data-testid="UserCell"]'
            ).length
        """) or 0
        if cell_count > 10:
            print(f"  [tab] primaryColumn cells after click: {cell_count} "
                  f"({time.time() - start:.1f}s)")
            break
        time.sleep(1)

    return {"timeline_label": timeline_label, "tab_clicked": tab_clicked,
            "cell_count": cell_count}


# ---------------------------------------------------------------------------
# User extraction — structural innerText parsing
# ---------------------------------------------------------------------------
EXTRACT_USERS_JS = r"""
(() => {
    const STRIP_TOKENS = new Set([
        "Follows you", "Follow", "Follow back", "Following",
        "Unfollow", "Pending"
    ]);
    const out = [];
    document.querySelectorAll('[data-testid="UserCell"]').forEach((cell) => {
        // --- Handle from UserAvatar data-testid (deterministic, per brief) ---
        let handle = null;
        const avatarEl = cell.querySelector('[data-testid^="UserAvatar-Container-"]');
        if (avatarEl) {
            const testid = avatarEl.getAttribute('data-testid');
            handle = testid.replace('UserAvatar-Container-', '');
        }
        // Fallback: href regex (only if UserAvatar testid missing)
        if (!handle) {
            const links = cell.querySelectorAll('a[href]');
            for (const a of links) {
                const href = a.getAttribute('href') || '';
                if (href.match(/^\/[A-Za-z0-9_]{3,15}$/) && !href.includes('/status/')) {
                    handle = href.slice(1);
                    break;
                }
            }
        }
        if (!handle) return;

        // --- Structural innerText parsing (per brief fact #4) ---
        const rawText = cell.innerText || '';
        const lines = rawText.split('\n').map(l => l.trim()).filter(l => l.length > 0);

        // Anchor on the @handle line index
        let handleIdx = -1;
        for (let i = 0; i < lines.length; i++) {
            if (lines[i] === '@' + handle) {
                handleIdx = i;
                break;
            }
        }
        if (handleIdx === -1) return;  // Can't anchor — skip this cell

        // Name = line immediately BEFORE @handle (may be 1 char — legitimate)
        const name = handleIdx > 0 ? lines[handleIdx - 1] : '';

        // follows_you: X gives us the mutual signal directly (brief fact #5)
        const follows_you = lines.includes('Follows you');

        // Bio = lines after handle line, minus known non-bio tokens
        const bioLines = [];
        for (let i = handleIdx + 1; i < lines.length; i++) {
            const line = lines[i];
            if (STRIP_TOKENS.has(line)) continue;
            if (line.startsWith('Click to ')) continue;
            // Skip bare count fragments like "1.2K" or date fragments
            if (/^\d+[KMB]?$/.test(line)) continue;
            if (/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/.test(line)) continue;
            if (/^\d+[hmd]$/.test(line)) continue;
            bioLines.push(line);
        }
        const bio = bioLines.join('\n');

        // Verified badge (keep existing logic — works)
        const verified = !!cell.querySelector('[data-testid="icon-verified"]') ||
                         !!cell.querySelector('svg[aria-label*="Verified" i]');

        // Profile image (keep existing logic — works)
        const imgEl = cell.querySelector('img[src*="profile_images"]');
        const avatar = imgEl ? imgEl.src : null;

        // Follower/following counts and location are NOT in the list view.
        // Emit null — do not fabricate (brief fact #7).
        out.push({
            handle,
            name,
            bio: bio || null,
            follows_you,
            followers_count: null,
            following_count: null,
            verified,
            avatar,
            location: null
        });
    });
    return out;
})()
"""


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
    if (el) el.scrollTop = el.scrollHeight;
    window.scrollTo(0, document.body.scrollHeight);
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
    let caught_up = false;
    if (document.body) {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
            acceptNode(n) {
                return /(all caught up|no more|end of|nothing more)/i.test(n.textContent || '')
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


def extract_users(page):
    return js(page, EXTRACT_USERS_JS) or []


def scroll_and_extract(page, label="users", max_scrolls=1000):
    """Scroll to bottom, accumulating all users that pass through the viewport."""
    collected = {}  # handle -> user data
    prev_scroll_height = 0
    no_gain = 0
    reconnect_delay = 2

    # Hydration: poll for UserCells BEFORE first scroll (brief fact #1)
    # Determine expected path from label for robust hydration
    expected_path = "/followers" if label == "followers" else "/following"
    hydrated = wait_for_list_hydration(page, expected_path)
    print(f"  [{label}] hydrated: {hydrated} UserCells")

    for i in range(max_scrolls):
        try:
            st = js(page, SCROLL_JS)
        except (ConnectionResetError, BrokenPipeError, OSError) as e:
            print(f"  [{label}] scroll {i+1}: connection dropped ({e}); reconnecting...")
            try:
                page.reconnect()
            except Exception:
                break
            time.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, 10)
            continue

        time.sleep(SCROLL_WAIT)

        try:
            batch = extract_users(page) or []
        except (ConnectionResetError, BrokenPipeError, OSError):
            print(f"  [{label}] scroll {i+1}: connection dropped during extract; reconnecting...")
            try:
                page.reconnect()
            except Exception:
                break
            time.sleep(reconnect_delay)
            continue

        new = 0
        for u in batch:
            h = u.get("handle")
            if h and h not in collected:
                collected[h] = u
                new += 1

        sh = int(st.get("scrollHeight") or 0)
        at_bottom = bool(st.get("at_bottom"))
        print(f"  [{label}] scroll {i+1}: +{new} new (total {len(collected)}) [scrollH={sh}, at_bottom={at_bottom}]")

        if sh == prev_scroll_height and sh > 0:
            no_gain += 1
            if no_gain >= CONSECUTIVE_EXHAUST:
                print(f"  [{label}] scrollHeight stable for {CONSECUTIVE_EXHAUST} iterations -> done")
                break
        else:
            no_gain = 0
        prev_scroll_height = sh
        reconnect_delay = 2  # reset on success

    return list(collected.values())


# ---------------------------------------------------------------------------
# Profile detection
# ---------------------------------------------------------------------------
def detect_username(page):
    """Detect the logged-in user's handle from the profile icon link."""
    return js(page, """
    (() => {
        const links = document.querySelectorAll('a[href]');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            if (href.match(/^\/[A-Za-z0-9_]{3,15}$/)) {
                const label = a.getAttribute('aria-label') || '';
                if (label.toLowerCase().includes('profile') || label.toLowerCase().includes('your')) {
                    return href.slice(1);
                }
            }
        }
        const sidebar = document.querySelector('[data-testid="AppTabBar_Profile_Link"]');
        if (sidebar) {
            const href = sidebar.getAttribute('href') || '';
            if (href.startsWith('/')) return href.slice(1);
        }
        return null;
    })()
    """)


# ---------------------------------------------------------------------------
# Enrich (opt-in, default OFF — visits individual profiles for counts/location)
# ---------------------------------------------------------------------------
def enrich_user(page, handle):
    """Visit a user's profile and extract counts + location. Opt-in only."""
    page.send("Page.navigate", {"url": f"https://x.com/{handle}"})
    wait_for_element(page, 'div[data-testid="UserName"]', ceiling=15)
    info = js(page, """
    (() => {
        const text = document.innerText || '';
        const followerMatch = text.match(/([\\d.,]+[KMB]?)\\s*Followers?\\b/i);
        const followingMatch = text.match(/([\\d.,]+[KMB]?)\\s*Following\\b/i);
        const locMatch = text.match(/📍\\s*(.+?)(?:\\n|$)/);
        return {
            followers_count: followerMatch ? followerMatch[1] : null,
            following_count: followingMatch ? followingMatch[1] : null,
            location: locMatch ? locMatch[1].trim() : null
        };
    })()
    """)
    return info


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract X followers/following via CDP")
    parser.add_argument("--username", default=None, help="Twitter handle (auto-detect if omitted)")
    parser.add_argument("--browser", default="brave", help="Browser to use (brave|chrome)")
    parser.add_argument("--profile", default="Default", help="Browser profile name")
    parser.add_argument("--output", default="data/x_graph_clean.json", help="Output JSON path")
    parser.add_argument("--max-scrolls", default=MAX_SCROLLS, type=int)
    parser.add_argument("--enrich", default=0, type=int,
                        help="Opt-in: visit top N profiles for counts/location (default 0=off)")
    args = parser.parse_args()

    max_scrolls = args.max_scrolls

    # 1. Resolve cookie DB
    cookie_db = resolve_cookie_db(args.browser, args.profile)
    if not cookie_db:
        raise RuntimeError(f"Cookie DB not found for {args.browser}/{args.profile}")
    print(f"[graph] Using cookie DB: {cookie_db}")

    # 2. Copy cookies into temp profile
    tmp = tempfile.mkdtemp(prefix="reapx_graph_")
    default_dir = os.path.join(tmp, "Default")
    os.makedirs(default_dir, exist_ok=True)
    shutil.copy2(cookie_db, os.path.join(default_dir, "Cookies"))
    print(f"[graph] Temp profile: {tmp}")

    # 3. Launch headless browser over CDP
    port = find_free_port()
    proc, _ = launch(args.browser, tmp, port)
    print(f"[graph] Launched {args.browser} on CDP port {port}")

    browser_cdp = None
    page = None
    try:
        version = wait_for_cdp(port)
        browser_cdp = CDP(version["webSocketDebuggerUrl"])
        page = new_page(browser_cdp, port)

        # 4. Navigate to X and detect username
        print("[graph] Navigating to x.com...")
        page.send("Page.navigate", {"url": "https://x.com/home"})
        wait_for_element(page, '[data-testid="AppTabBar_Profile_Link"]', ceiling=30)

        title = js(page, "document.title")
        href = js(page, "location.href")
        print(f"[graph] Loaded: '{title}' @ {href}")

        if "login" in (href or "") or "onboarding" in (href or ""):
            raise RuntimeError("Not authenticated — copied session rejected")

        username = args.username
        if not username:
            print("[graph] Detecting username...")
            time.sleep(2)
            username = detect_username(page)
            if not username:
                page.send("Page.navigate", {"url": "https://x.com/me"})
                wait_for_element(page, '[data-testid="AppTabBar_Profile_Link"]', ceiling=20)
                href = js(page, "location.href")
                m = (href or "").rstrip("/").split("/")
                username = m[-1] if m else None
            print(f"[graph] Detected username: @{username}")

        if not username:
            raise RuntimeError("Could not detect username — pass --username")

        # 5. Get profile info
        print(f"[graph] Navigating to @{username}'s profile...")
        page.send("Page.navigate", {"url": f"https://x.com/{username}"})
        wait_for_element(page, 'div[data-testid="UserName"]', ceiling=20)

        profile_info = js(page, """
        (() => {
            const text = document.innerText || '';
            const followerMatch = text.match(/([\\d.,]+[KMB]?)\\s*Followers?\\b/i);
            const followingMatch = text.match(/([\\d.,]+[KMB]?)\\s*Following\\b/i);
            const nameEl = document.querySelector('div[data-testid="UserName"] span');
            const name = nameEl ? nameEl.innerText.trim() : null;
            const bioEl = document.querySelector('div[data-testid="UserDescription"]');
            const bio = bioEl ? bioEl.innerText.trim() : null;
            return {
                name,
                bio,
                followers_count: followerMatch ? followerMatch[1] : null,
                following_count: followingMatch ? followingMatch[1] : null
            };
        })()
        """)
        print(f"[graph] Profile: {json.dumps(profile_info, indent=2)}")

        # Parse total followers from profile for meta
        profile_followers_total = None
        fc = (profile_info or {}).get("followers_count")
        if fc:
            profile_followers_total = fc  # e.g. "4.7K"

        # 6. Extract followers (with retry if X rate-limits the endpoint)
        # X may serve a truncated/rate-limited page on repeated hits. Retry
        # with a longer delay + profile warm-up to get a fresh session.
        print(f"[graph] Navigating to @{username}/followers...")
        page.send("Page.navigate", {"url": f"https://x.com/{username}/followers"})
        # FIX: X renders 'Verified Followers' tab by default. Click 'Followers' tab.
        tab_info = click_followers_tab(page)
        followers = scroll_and_extract(page, "followers", max_scrolls=max_scrolls)

        if len(followers) < 50:
            print(f"[graph] Follower count suspiciously low ({len(followers)}); retrying with fresh navigation after 20s...")
            time.sleep(20)
            # Warm-up: visit profile first to reset session state
            # FIX: websocket may have died during the sleep; reconnect on BrokenPipeError
            try:
                page.send("Page.navigate", {"url": f"https://x.com/{username}"})
            except (BrokenPipeError, ConnectionResetError, OSError):
                print("  [retry] websocket dead during warm-up; reconnecting...")
                try:
                    page.reconnect()
                except Exception:
                    print("  [retry] reconnect failed; skipping retry")
                    followers = followers  # keep partial
                else:
                    page.send("Page.navigate", {"url": f"https://x.com/{username}"})
                    wait_for_element(page, 'div[data-testid="UserName"]', ceiling=20)
                    time.sleep(3)
                    page.send("Page.navigate", {"url": f"https://x.com/{username}/followers"})
                    tab_info = click_followers_tab(page)
                    followers = scroll_and_extract(page, "followers", max_scrolls=max_scrolls)
            else:
                wait_for_element(page, 'div[data-testid="UserName"]', ceiling=20)
                time.sleep(3)
                page.send("Page.navigate", {"url": f"https://x.com/{username}/followers"})
                tab_info = click_followers_tab(page)
                followers = scroll_and_extract(page, "followers", max_scrolls=max_scrolls)

        print(f"[graph] Extracted {len(followers)} followers")

        # 7. Extract following (with partial-save on crash + retry on connection drop)
        following = []
        try:
            print(f"[graph] Navigating to @{username}/following...")
            page.send("Page.navigate", {"url": f"https://x.com/{username}/following"})
            following = scroll_and_extract(page, "following", max_scrolls=max_scrolls)
            print(f"[graph] Extracted {len(following)} following")
        except Exception as e:
            print(f"[graph] WARNING: following extraction failed: {e}")
            print(f"[graph] Saving partial results ({len(followers)} followers only)")
            following = []

        # Retry following if connection crash truncated it (< 500 cells)
        if len(following) < 500:
            print(f"[graph] Following count low ({len(following)}); retrying after 60s...")
            time.sleep(60)
            try:
                page.send("Page.navigate", {"url": f"https://x.com/{username}/following"})
                following = scroll_and_extract(page, "following", max_scrolls=max_scrolls)
                print(f"[graph] Extracted {len(following)} following (retry)")
            except Exception as e:
                print(f"[graph] WARNING: following retry failed: {e}")
                following = following  # keep partial

        # 8. Optional enrich (opt-in, default OFF — brief fact #7)
        if args.enrich > 0:
            print(f"[graph] Enriching top {args.enrich} followers...")
            for u in followers[:args.enrich]:
                info = enrich_user(page, u["handle"])
                u.update(info)
                time.sleep(3)  # >=3s delay to avoid rate-limiting

        # 9. Merge with existing output if present — keep the best of all runs
        # X rate-limits on repeated runs; this preserves the largest follower
        # and following counts seen across any run instead of overwriting.
        output_path = Path(args.output)
        existing_followers = []
        existing_following = []
        if output_path.exists():
            try:
                with open(output_path) as f:
                    existing = json.load(f)
                existing_followers = existing.get('followers', [])
                existing_following = existing.get('following', [])
                print(f"[graph] Existing output found: {len(existing_followers)} followers, {len(existing_following)} following")
            except Exception:
                existing_followers = []
                existing_following = []

        # Quality-aware merge: union by handle, keep record with better name
        def merge_users(existing_list, new_list):
            merged = {u['handle']: u for u in existing_list if u.get('handle')}
            for u in new_list:
                h = u.get('handle')
                if not h:
                    continue
                if h not in merged:
                    merged[h] = u
                else:
                    # Keep the one with the better name (non-empty beats empty)
                    existing_name = merged[h].get('name', '') or ''
                    new_name = u.get('name', '') or ''
                    if len(new_name) > len(existing_name):
                        merged[h] = u
            return list(merged.values())

        followers = merge_users(existing_followers, followers)
        following = merge_users(existing_following, following)
        print(f"[graph] After merge: {len(followers)} followers, {len(following)} following")

        # 10. Build output with meta block
        output = {
            "username": username,
            "profile": profile_info,
            "followers": followers,
            "following": following,
            "stats": {
                "followers_count": len(followers),
                "following_count": len(following),
                "mutual_count": len(set(u["handle"] for u in followers) & set(u["handle"] for u in following))
            },
            "meta": {
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "followers_extracted": len(followers),
                "following_extracted": len(following),
                "profile_followers_total": profile_followers_total,
                "ceiling_note": "X serves a bounded window of the follower/following timeline per session; totals below the profile count are expected. NOTE: /followers renders the 'Verified Followers' tab by default — the 'Followers' tab must be clicked, otherwise only verified followers are returned (historically produced a false '3 followers' result).",
                "fields_unavailable": ["followers_count", "following_count", "location"]
            }
        }

        # 11. Atomic write
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = output_path.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
        os.replace(tmp_file, output_path)
        print(f"[graph] Wrote {len(followers)} followers + {len(following)} following -> {output_path}")

        # 11. Self-report
        print("\n" + "="*60)
        print("SELF-REPORT — X Graph Extraction")
        print("="*60)
        print(f"  followers extracted:  {len(followers)}")
        print(f"  following extracted:  {len(following)}")
        print(f"  mutual count:         {output['stats']['mutual_count']}")

        # Name quality
        fn = [u for u in followers if u.get("name")]
        print(f"  followers with name:  {len(fn)}/{len(followers)} ({100*len(fn)/max(1,len(followers)):.1f}%)")
        # Bio quality
        fb = [u for u in followers if u.get("bio")]
        print(f"  followers with bio:   {len(fb)}/{len(followers)} ({100*len(fb)/max(1,len(followers)):.1f}%)")
        # Click to Follow check (must be 0)
        click_follow = [u for u in followers if 'Click to Follow' in ((u.get('bio') or '') + (u.get('name') or ''))]
        print(f"  'Click to Follow' in followers: {len(click_follow)}")
        # Click to Unfollow check (must be 0)
        click_unfollow = [u for u in followers if 'Click to Unfollow' in ((u.get('bio') or '') + (u.get('name') or ''))]
        print(f"  'Click to Unfollow' in followers: {len(click_unfollow)}")
        # follows_you
        fyu = [u for u in followers if u.get("follows_you")]
        print(f"  followers with follows_you=true: {len(fyu)}")
        print(f"  follows_you present on all: {all('follows_you' in u for u in followers)}")
        print("="*60)

    finally:
        if page:
            page.close()
        if browser_cdp:
            browser_cdp.close()
        cleanup(proc, tmp)


if __name__ == "__main__":
    main()
