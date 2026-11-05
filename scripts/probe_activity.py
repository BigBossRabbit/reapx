#!/usr/bin/env python3
"""probe_activity.py — probe X profile pages for activity signals.

Visits each profile page to determine if an account is ALIVE or DEAD.
Reuses the CDP + cookie-copy architecture from fetch_x_graph.py.

For each handle navigates to https://x.com/<handle> and extracts:
  exists, suspended, protected, posts_count, followers_count, following_count,
  joined, last_post_iso, pinned_note, bio, location, website.

CRITICAL: if the first timeline post is PINNED, its date is NOT the last
activity date. The script detects the pinned indicator and reads the next
post's time element instead.

Usage:
    python3 probe_activity.py --tier T5_VIP_OUTREACH --limit 40
    python3 probe_activity.py --resume --limit 100
"""
import argparse
import json
import os
import random
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import websocket
except ImportError:
    sys.exit("ERROR: 'websocket-client' required. Run: pip3 install websocket-client")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_x_graph import CDP, js, wait_for_cdp, new_page, wait_for_element
from browser_locator import resolve_cookie_db
from cdp_launcher import launch, find_free_port, cleanup

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SAVE_EVERY = 25
RATE_LIMIT_BACKOFF = 60
MAX_CONSECUTIVE_RATE_LIMITS = 5
PROFILE_LOAD_TIMEOUT = 30
MIN_PAGE_WAIT = 3

# Default tier priority order (highest value first)
TIER_ORDER = ['T5_VIP_OUTREACH', 'T1_INNER_CIRCLE', 'T2_WARM', 'T3_ASPIRATIONAL', 'T4_POTENTIAL']

# Repo data dir (script lives in <repo>/scripts/).
_DATA = Path(__file__).resolve().parent.parent / 'data'
CRM_PATH = str(_DATA / 'x_crm_scored.json')
DEFAULT_OUTPUT = str(_DATA / 'x_activity.json')

# ---------------------------------------------------------------------------
# Profile extraction JS
# ---------------------------------------------------------------------------
EXTRACT_PROFILE_JS = r"""
(() => {
    const text = (document.body && document.body.innerText) || '';

    // --- Helper: parse 1.2K / 3.4M suffixes into ints ---
    function parseSuffix(s) {
        if (s == null) return null;
        s = String(s).replace(/,/g, '').trim();
        const m = s.match(/^([\d.]+)\s*([KMB]?)$/i);
        if (!m) return null;
        let num = parseFloat(m[1]);
        if (isNaN(num)) return null;
        const suffix = (m[2] || '').toUpperCase();
        if (suffix === 'K') num *= 1000;
        else if (suffix === 'M') num *= 1000000;
        else if (suffix === 'B') num *= 1000000000;
        return Math.round(num);
    }

    // --- Suspended / nonexistent ---
    const suspended = /account suspended/i.test(text);
    const doesntExist = /this account doesn.t exist/i.test(text);

    if (suspended) {
        return {exists: false, suspended: true, protected: false,
                posts_count: null, followers_count: null, following_count: null,
                joined: null, last_post_iso: null, pinned_note: false,
                bio: null, location: null, website: null};
    }
    if (doesntExist) {
        return {exists: false, suspended: false, protected: false,
                posts_count: null, followers_count: null, following_count: null,
                joined: null, last_post_iso: null, pinned_note: false,
                bio: null, location: null, website: null};
    }

    // --- Protected ---
    const protected_ = /these posts are protected/i.test(text);

    // --- Stats ---
    let posts_count = null;
    const postsMatch = text.match(/([\d.,]+[KMB]?)\s*(?:posts?|tweets?)/i);
    if (postsMatch) posts_count = parseSuffix(postsMatch[1]);

    let followers_count = null;
    const followerMatch = text.match(/([\d.,]+[KMB]?)\s*Followers?/i);
    if (followerMatch) followers_count = parseSuffix(followerMatch[1]);

    let following_count = null;
    const followingMatch = text.match(/([\d.,]+[KMB]?)\s*Following\b/i);
    if (followingMatch) following_count = parseSuffix(followingMatch[1]);

    // --- Joined ---
    let joined = null;
    const joinedMatch = text.match(/Joined\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4})/i);
    if (joinedMatch) joined = joinedMatch[1].trim();

    // --- Bio ---
    const bioEl = document.querySelector('div[data-testid="UserDescription"]');
    const bio = bioEl ? bioEl.innerText.trim() : null;

    // --- Location ---
    let location = null;
    const locMatch = text.match(/(?:[\u260E\u{1F4CD}\u{1F4E7}\u{1F517}\u{1F4CC}]|(?:\u00a0|\s)+[A-Za-z][A-Za-z\s,]+?(?:\n|$))/u);
    if (locMatch) {
        let loc = locMatch[0].replace(/^[^\w]+/, '').trim();
        if (loc && !/^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b/.test(loc)) {
            location = loc;
        }
    }

    // --- Website ---
    let website = null;
    const websiteEl = document.querySelector('a[title*="http"], a[rel="nofollow"]');
    if (websiteEl) {
        website = websiteEl.href || websiteEl.getAttribute('title');
    }

    // --- Timeline ---
    let last_post_iso = null;
    let pinned_note = false;

    const articles = document.querySelectorAll('article[data-testid="tweet"]');
    if (articles.length > 0) {
        // Check first article for pinned indicator
        const firstArticle = articles[0];
        const firstText = firstArticle.innerText || '';
        if (/pinned/i.test(firstText)) pinned_note = true;

        // Collect time elements from all articles
        const timeEls = [];
        for (const article of articles) {
            const t = article.querySelector('time[datetime]');
            if (t) timeEls.push(t);
        }

        if (timeEls.length > 0) {
            if (pinned_note && timeEls.length > 1) {
                last_post_iso = timeEls[1].getAttribute('datetime');
            } else {
                last_post_iso = timeEls[0].getAttribute('datetime');
            }
        }
    }

    return {
        exists: true,
        suspended: false,
        protected: protected_,
        posts_count: posts_count,
        followers_count: followers_count,
        following_count: following_count,
        joined: joined,
        last_post_iso: last_post_iso,
        pinned_note: pinned_note,
        bio: bio,
        location: location,
        website: website
    };
})()
"""

# ---------------------------------------------------------------------------
# Status-check JS (for polling after navigation)
# ---------------------------------------------------------------------------
STATUS_JS = r"""
(() => {
    const text = (document.body && document.body.innerText) || '';
    const title = document.title || '';
    if (!text || text.trim().length < 15) return 'empty';
    if (/something went wrong/i.test(text) || /something went wrong/i.test(title)) return 'rate_limited';
    if (/account suspended/i.test(text)) return 'suspended';
    if (/this account doesn.t exist/i.test(text)) return 'not_exist';
    if (document.querySelector('div[data-testid="UserName"]')) return 'loaded';
    if (/these posts are protected/i.test(text)) return 'protected';
    return 'loading';
})()
"""


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="Probe X profile pages for activity signals")
    parser.add_argument('--tier', default=None,
                        help="Filter by tier (comma-separated, e.g. T5_VIP_OUTREACH,T1_INNER_CIRCLE)")
    parser.add_argument('--limit', type=int, default=50, help="Max profiles to probe")
    parser.add_argument('--resume', action='store_true',
                        help="Skip handles already present in output")
    parser.add_argument('--output', default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument('--browser', default='brave', help="Browser to use")
    parser.add_argument('--profile', default='Default', help="Browser profile name")
    return parser.parse_args()


def build_record(handle, tier, influence_score, result):
    """Build a standardized record from extraction result."""
    return {
        'handle': handle,
        'tier': tier,
        'influence_score': influence_score,
        'exists': result.get('exists') if result else None,
        'suspended': result.get('suspended') if result else None,
        'protected': result.get('protected') if result else None,
        'posts_count': result.get('posts_count') if result else None,
        'followers_count': result.get('followers_count') if result else None,
        'following_count': result.get('following_count') if result else None,
        'joined': result.get('joined') if result else None,
        'last_post_iso': result.get('last_post_iso') if result else None,
        'pinned_note': result.get('pinned_note') if result else None,
        'bio': result.get('bio') if result else None,
        'location': result.get('location') if result else None,
        'website': result.get('website') if result else None,
        'error': result.get('error') if result else None,
    }


def save_output(path, records):
    """Atomic-save output JSON."""
    output = {
        'probed_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'records': records
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = path.with_suffix('.json.tmp')
    tmp_file.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    os.replace(tmp_file, path)


def probe_profile(page, handle):
    """Navigate to a profile and extract activity signals.

    Returns:
        dict with extracted fields, or
        {'_rate_limited': True} if rate limited, or
        None if extraction failed entirely.
    """
    url = f"https://x.com/{handle}"

    # Navigate
    try:
        page.send("Page.navigate", {"url": url})
    except (ConnectionResetError, BrokenPipeError, OSError) as e:
        print(f"  [probe] connection dropped during navigate ({e}); reconnecting...")
        try:
            page.reconnect()
        except Exception:
            return {'_rate_limited': True}
        try:
            page.send("Page.navigate", {"url": url})
        except Exception:
            return {'_rate_limited': True}

    # Poll for page state
    start = time.time()
    status = 'loading'
    first_check = True

    while time.time() - start < PROFILE_LOAD_TIMEOUT:
        try:
            status = js(page, STATUS_JS)
        except (ConnectionResetError, BrokenPipeError, OSError):
            print(f"  [probe] connection dropped during status check; reconnecting...")
            try:
                page.reconnect()
            except Exception:
                return {'_rate_limited': True}
            time.sleep(1)
            continue

        # On first iteration, always wait minimum time
        if first_check:
            first_check = False
            if status == 'loading' or status == 'empty':
                time.sleep(MIN_PAGE_WAIT)
                continue

        if status in ('rate_limited', 'suspended', 'not_exist', 'protected', 'loaded'):
            break

        time.sleep(1)

    # Handle states
    if status == 'rate_limited':
        return {'_rate_limited': True}

    if status == 'suspended':
        return {'exists': False, 'suspended': True}

    if status == 'not_exist':
        return {'exists': False, 'suspended': False}

    if status == 'protected':
        # Protected: extract profile data (no timeline access)
        try:
            result = js(page, EXTRACT_PROFILE_JS)
            if result:
                result['protected'] = True
            return result
        except Exception:
            return None

    if status == 'loaded':
        try:
            return js(page, EXTRACT_PROFILE_JS)
        except Exception:
            return None

    # Timeout — treat as rate limited to be safe
    return {'_rate_limited': True}


def main():
    args = parse_args()

    # --- Load CRM data ---
    crm_path = Path(CRM_PATH)
    if not crm_path.exists():
        print(f"ERROR: CRM file not found: {crm_path}")
        sys.exit(1)

    with open(crm_path) as f:
        crm = json.load(f)

    nodes = crm['nodes']

    # --- Filter by tier ---
    if args.tier:
        tiers = [t.strip() for t in args.tier.split(',')]
        nodes = [n for n in nodes if n.get('tier') in tiers]

    # --- Sort: tier priority first, then influence_score descending ---
    tier_rank = {t: i for i, t in enumerate(TIER_ORDER)}
    nodes.sort(key=lambda n: (
        tier_rank.get(n.get('tier'), 99),
        -n.get('influence_score', 0)
    ))

    # --- Apply limit ---
    nodes = nodes[:args.limit]

    # --- Load existing output for resume ---
    output_path = Path(args.output)
    existing_records = []
    probed_handles = set()
    if args.resume and output_path.exists():
        try:
            with open(output_path) as f:
                existing = json.load(f)
            existing_records = existing.get('records', [])
            probed_handles = {r['handle'] for r in existing_records if r.get('handle')}
            nodes = [n for n in nodes if n['handle'] not in probed_handles]
            print(f"[probe] Resuming: {len(existing_records)} existing, {len(nodes)} remaining")
        except Exception as e:
            print(f"[probe] WARNING: could not load existing output: {e}")
            existing_records = []

    print(f"[probe] CRM: {len(crm['nodes'])} total | probing {len(nodes)} | output: {output_path}")

    if not nodes:
        print("[probe] Nothing to probe.")
        return

    # --- Kill stale CDP browsers (best-effort, argv-list: no shell) ---
    print("[probe] Killing stale CDP browsers...")
    try:
        import subprocess
        subprocess.run(["pkill", "-f", "remote-debugging-port"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
    except Exception:
        pass
    time.sleep(1)

    # --- Launch browser with cookies ---
    cookie_db = resolve_cookie_db(args.browser, args.profile)
    if not cookie_db:
        print(f"ERROR: Cookie DB not found for {args.browser}/{args.profile}")
        sys.exit(1)
    print(f"[probe] Cookie DB: {cookie_db}")

    tmp = tempfile.mkdtemp(prefix="reapx_probe_")
    default_dir = os.path.join(tmp, "Default")
    os.makedirs(default_dir, exist_ok=True)
    shutil.copy2(cookie_db, os.path.join(default_dir, "Cookies"))

    port = find_free_port()
    proc, _ = launch(args.browser, tmp, port)
    print(f"[probe] Launched {args.browser} on CDP port {port}")

    browser_cdp = None
    page = None
    records = []
    consecutive_rate_limits = 0

    try:
        version = wait_for_cdp(port)
        browser_cdp = CDP(version["webSocketDebuggerUrl"])
        page = new_page(browser_cdp, port)

        # --- Verify authentication ---
        print("[probe] Verifying authentication...")
        page.send("Page.navigate", {"url": "https://x.com/home"})
        wait_for_element(page, '[data-testid="AppTabBar_Profile_Link"]', ceiling=30)

        href = js(page, "location.href") or ""
        if "login" in href or "onboarding" in href:
            print("ERROR: Not authenticated — copied session rejected")
            sys.exit(1)

        print(f"[probe] Authenticated @ {href}")

        # --- Probe loop ---
        for i, node in enumerate(nodes):
            handle = node['handle']
            tier = node.get('tier')
            influence_score = node.get('influence_score')

            print(f"\n[{i+1}/{len(nodes)}] @{handle} | {tier} | score={influence_score}")

            # Stop condition: too many consecutive rate limits
            if consecutive_rate_limits >= MAX_CONSECUTIVE_RATE_LIMITS:
                print(f"  [probe] STOPPED — {consecutive_rate_limits} consecutive rate limits")
                record = build_record(handle, tier, influence_score, None)
                record['error'] = 'run_stopped_rate_limited'
                records.append(record)
                continue

            # --- Probe the profile ---
            try:
                result = probe_profile(page, handle)
            except Exception as e:
                print(f"  [probe] ERROR: {e}")
                result = None

            # --- Handle rate limiting ---
            if result is None:
                # Complete failure — treat as rate limit
                consecutive_rate_limits += 1
                print(f"  [probe] FAILED (treated as rate limit, {consecutive_rate_limits} consecutive)")
                record = build_record(handle, tier, influence_score, None)
                record['error'] = 'rate_limited'
                records.append(record)
                continue

            if result.get('_rate_limited'):
                consecutive_rate_limits += 1
                print(f"  [probe] RATE LIMITED ({consecutive_rate_limits} consecutive)")

                # Back off and retry once
                print(f"  [probe] Backing off {RATE_LIMIT_BACKOFF}s...")
                time.sleep(RATE_LIMIT_BACKOFF)

                try:
                    result = probe_profile(page, handle)
                except Exception as e:
                    print(f"  [probe] Retry ERROR: {e}")
                    result = None

                if result is None or result.get('_rate_limited'):
                    # Still rate limited after retry — write error record
                    record = build_record(handle, tier, influence_score, None)
                    record['error'] = 'rate_limited'
                    records.append(record)
                    print(f"  [probe] Retry failed — wrote rate_limited record")
                else:
                    # Retry succeeded
                    consecutive_rate_limits = 0
                    record = build_record(handle, tier, influence_score, result)
                    records.append(record)
                    alive = "ALIVE" if record.get('last_post_iso') else "DEAD"
                    print(f"  [probe] Retry succeeded — {alive}")
            else:
                # Success
                consecutive_rate_limits = 0
                record = build_record(handle, tier, influence_score, result)
                records.append(record)

                # Print summary
                alive = "ALIVE" if record.get('last_post_iso') else "DEAD"
                suspended = "SUSPENDED" if record.get('suspended') else ""
                protected = "PROTECTED" if record.get('protected') else ""
                exists = "NOT_FOUND" if record.get('exists') == False else ""
                print(f"  [probe] {alive} {suspended} {protected} {exists}")
                print(f"           posts={record.get('posts_count')} "
                      f"followers={record.get('followers_count')} "
                      f"following={record.get('following_count')}")
                print(f"           last_post={record.get('last_post_iso')} "
                      f"pinned={record.get('pinned_note')} "
                      f"joined={record.get('joined')}")

            # --- Append-save every SAVE_EVERY profiles ---
            if len(records) % SAVE_EVERY == 0:
                all_records = existing_records + records
                save_output(output_path, all_records)
                print(f"  [probe] === SAVED {len(records)} new / {len(all_records)} total ===")

            # --- Random delay between profiles ---
            delay = random.uniform(2.5, 4.5)
            print(f"  [probe] Sleeping {delay:.1f}s...")
            time.sleep(delay)

    finally:
        # --- Cleanup ---
        if page:
            page.close()
        if browser_cdp:
            browser_cdp.close()
        cleanup(proc, tmp)

    # --- Final save ---
    all_records = existing_records + records
    save_output(output_path, all_records)

    # --- Summary ---
    alive_count = sum(1 for r in records if r.get('last_post_iso'))
    dead_count = sum(1 for r in records
                     if not r.get('last_post_iso')
                     and r.get('exists') != False
                     and not r.get('error'))
    not_found = sum(1 for r in records if r.get('exists') == False)
    rate_limited_count = sum(1 for r in records if r.get('error') == 'rate_limited')
    stopped_count = sum(1 for r in records if r.get('error') == 'run_stopped_rate_limited')

    print("\n" + "=" * 60)
    print("PROBE ACTIVITY — BATTLE REPORT")
    print("=" * 60)
    print(f"  profiles probed:    {len(records)}")
    print(f"  ALIVE:              {alive_count}")
    print(f"  DEAD:               {dead_count}")
    print(f"  NOT FOUND:          {not_found}")
    print(f"  RATE LIMITED:       {rate_limited_count}")
    print(f"  RUN STOPPED:        {stopped_count}")
    print(f"  output file:        {output_path}")
    print(f"  total records:      {len(all_records)}")
    print("=" * 60)


if __name__ == "__main__":
    main()
