#!/usr/bin/env python3
"""probe_2hop.py — harvest the follower lists of YOUR top accounts (2-hop).

WHY: betweenness centrality is dormant in a 1-hop star graph — every path runs
through you, so nobody "bridges" anything. Adding hop-2 edges (who your contacts
follow) creates real alternate paths, which is what makes bridge detection and
community structure meaningful.

Reuses the proven CDP + cookie machinery from fetch_x_graph.py. Read-only.
"""
import argparse, json, os, random, shutil, sys, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_x_graph import CDP, js, wait_for_cdp, new_page, wait_for_element
from reapx_browser_locator import resolve_cookie_db
from reapx_cdp_launcher import launch, find_free_port

# Repo data dir (script lives in <repo>/scripts/).
BASE = str(Path(__file__).resolve().parent.parent / 'data') + '/'
OUT = BASE + 'x_2hop.json'

EXTRACT_CELLS = r"""
(() => {
  const col = document.querySelector('[data-testid="primaryColumn"]');
  if (!col) return '[]';
  const cells = [...col.querySelectorAll('[data-testid="UserCell"]')];
  const out = cells.map(c => {
    const a = c.querySelector('a[href^="/"]');
    const handle = a ? a.getAttribute('href').replace(/^\//, '').split('/')[0] : null;
    const txt = c.innerText || '';
    return { handle: handle, follows_you: /Follows you/i.test(txt) };
  }).filter(x => x.handle && !x.handle.includes('i/') && x.handle.length < 20);
  return JSON.stringify(out);
})()
"""

STATUS = r"""
(() => {
  const t = (document.body && document.body.innerText) || '';
  if (/something went wrong/i.test(t)) return 'rate_limited';
  if (/account suspended/i.test(t)) return 'suspended';
  if (/These accounts are protected|posts are protected/i.test(t)) return 'protected';
  const col = document.querySelector('[data-testid="primaryColumn"]');
  const n = col ? col.querySelectorAll('[data-testid="UserCell"]').length : 0;
  if (n > 0) return 'loaded';
  return 'loading';
})()
"""


def click_followers_tab(page):
    """The /following URL is unambiguous, but /followers defaults to the
    Verified Followers tab — must click the exact 'Followers' tab."""
    return js(page, r"""
    (() => {
      const tabs = [...document.querySelectorAll('[role="tab"], [data-testid="primaryColumn"] a')];
      for (const t of tabs) {
        if ((t.innerText || '').trim() === 'Followers') { t.click(); return 'clicked'; }
      }
      return 'not_found';
    })()
    """)


def harvest(page, url, max_scrolls, want_tab_click):
    page.send('Page.navigate', {'url': url})
    time.sleep(6)
    for _ in range(30):
        st = js(page, STATUS)
        if st in ('loaded', 'rate_limited', 'suspended', 'protected'):
            break
        time.sleep(1)
    st = js(page, STATUS)
    if st != 'loaded':
        return st, []

    if want_tab_click:
        click_followers_tab(page)
        time.sleep(4)

    seen, stall = {}, 0
    for _ in range(max_scrolls):
        try:
            batch = json.loads(js(page, EXTRACT_CELLS) or '[]')
        except Exception:
            batch = []
        before = len(seen)
        for b in batch:
            seen.setdefault(b['handle'], b)
        if len(seen) == before:
            stall += 1
            if stall >= 4:
                break
        else:
            stall = 0
        js(page, 'window.scrollBy(0, window.innerHeight*2); 1')
        time.sleep(1.4)
    return 'ok', list(seen.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tier', default='T5_VIP_OUTREACH,T1_INNER_CIRCLE')
    ap.add_argument('--limit', type=int, default=25)
    ap.add_argument('--max-scrolls', type=int, default=25)
    ap.add_argument('--direction', default='following',
                    choices=['following', 'followers'],
                    help="'following' is far more reliable (no tab trap)")
    ap.add_argument('--browser', default='brave')
    ap.add_argument('--resume', action='store_true')
    args = ap.parse_args()

    scored = json.load(open(BASE + 'x_crm_scored.json'))
    nodes = scored['nodes'] if isinstance(scored, dict) else scored
    tiers = [t.strip() for t in args.tier.split(',')]

    # prefer accounts we already KNOW are alive
    alive = set()
    try:
        ab = json.load(open(BASE + 'x_activity_bands.json'))
        alive = {r['handle'] for r in ab['records']
                 if r.get('band') in ('ACTIVE_7D', 'ACTIVE_30D', 'SLOWING_90D')}
    except Exception:
        pass

    cand = [n for n in nodes if n.get('tier') in tiers]
    cand.sort(key=lambda x: (x['handle'] not in alive, -(x.get('influence_score') or 0)))

    done = {}
    if args.resume and os.path.exists(OUT):
        try:
            prev = json.load(open(OUT))
            done = {k: v for k, v in prev.get('hubs', {}).items()}
        except Exception:
            done = {}

    todo = [n for n in cand if n['handle'] not in done][:args.limit]
    print(f'[2hop] {len(todo)} hubs to harvest ({args.direction}), {len(done)} already done')
    if not todo:
        print('[2hop] nothing to do')
        return

    # Kill stale CDP browsers (best-effort, argv-list form: no shell).
    try:
        import subprocess
        subprocess.run(["pkill", "-f", "remote-debugging-port"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
    except Exception:
        pass
    time.sleep(2)

    db = resolve_cookie_db(args.browser)
    tmp = tempfile.mkdtemp(prefix='reapx_2hop_')
    dd = os.path.join(tmp, 'Default')
    os.makedirs(dd, exist_ok=True)
    shutil.copy2(db, os.path.join(dd, 'Cookies'))
    port = find_free_port()
    proc, _ = launch(args.browser, tmp, port)

    hubs = dict(done)
    consec_rl = 0
    try:
        version = wait_for_cdp(port)
        browser = CDP(version['webSocketDebuggerUrl'])
        page = new_page(browser, port)
        page.send('Page.navigate', {'url': 'https://x.com/home'})
        wait_for_element(page, '[data-testid="AppTabBar_Profile_Link"]', ceiling=30)
        print('[2hop] authenticated')

        for i, n in enumerate(todo):
            h = n['handle']
            print(f"\n[{i+1}/{len(todo)}] @{h} | {n.get('tier')} | score={n.get('influence_score')}")
            url = f'https://x.com/{h}/{args.direction}'
            st, cells = harvest(page, url, args.max_scrolls,
                                want_tab_click=(args.direction == 'followers'))
            if st == 'rate_limited':
                consec_rl += 1
                print(f'  RATE LIMITED ({consec_rl} consecutive) — backing off 60s')
                hubs[h] = {'status': 'rate_limited', 'edges': []}
                time.sleep(60)
                if consec_rl >= 4:
                    print('  STOPPING — sustained rate limit')
                    break
                continue
            consec_rl = 0
            hubs[h] = {'status': st, 'tier': n.get('tier'),
                       'influence_score': n.get('influence_score'),
                       'direction': args.direction,
                       'edges': [c['handle'] for c in cells]}
            print(f'  {st}: {len(cells)} edges')

            if (i + 1) % 5 == 0:
                json.dump({'harvested_at': datetime.now(timezone.utc).isoformat(),
                           'hubs': hubs}, open(OUT, 'w'), indent=1)
                print('  [saved]')
            time.sleep(random.uniform(3.0, 5.0))
    finally:
        json.dump({'harvested_at': datetime.now(timezone.utc).isoformat(),
                   'hubs': hubs}, open(OUT, 'w'), indent=1)
        try:
            proc.terminate()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    ok = [h for h, v in hubs.items() if v.get('edges')]
    tot = sum(len(v.get('edges') or []) for v in hubs.values())
    print(f'\n{"="*56}\n2-HOP HARVEST\n  hubs with edges: {len(ok)}\n'
          f'  total hop-2 edges: {tot}\n  output: {OUT}\n{"="*56}')


if __name__ == '__main__':
    main()
