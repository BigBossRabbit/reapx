#!/usr/bin/env python3
"""Independent check: is X really throttling PROFILE pages?"""
import sys, os, time, shutil, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_x_graph import CDP, js, wait_for_cdp, new_page, wait_for_element
from reapx_browser_locator import resolve_cookie_db
from reapx_cdp_launcher import launch, find_free_port

# The X handle to probe (pass one as the first argument).
HANDLE = sys.argv[1] if len(sys.argv) > 1 else 'target-handle'

# Best-effort cleanup of stale headless CDP browsers from an earlier crashed
# run (the standalone X-graph tools' own hygiene; the plugin's reapx_cdp_launcher
# path never needs this — it terminates its own process). argv-list form:
# no shell, no os.system.
try:
    import subprocess
    subprocess.run(["pkill", "-f", "remote-debugging-port"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)
except Exception:
    pass
time.sleep(2)

db = resolve_cookie_db('brave')
tmp = tempfile.mkdtemp(prefix='reapx_verify_')
dd = os.path.join(tmp, 'Default')
os.makedirs(dd, exist_ok=True)
shutil.copy2(db, os.path.join(dd, 'Cookies'))

port = find_free_port()
proc, _ = launch('brave', tmp, port)
try:
    version = wait_for_cdp(port)
    browser = CDP(version['webSocketDebuggerUrl'])
    page = new_page(browser, port)

    page.send('Page.navigate', {'url': 'https://x.com/home'})
    wait_for_element(page, '[data-testid="AppTabBar_Profile_Link"]', ceiling=30)
    print('AUTH href:', js(page, 'location.href'))

    page.send('Page.navigate', {'url': f'https://x.com/{HANDLE}'})
    time.sleep(14)

    print(js(page, """(() => {
      const t = document.body.innerText || '';
      const times = [...document.querySelectorAll('[data-testid="primaryColumn"] time')]
                      .map(e => e.getAttribute('datetime'));
      return JSON.stringify({
        len: t.length,
        wentWrong: t.includes('Something went wrong'),
        notExist: t.includes('exist'),
        suspended: t.includes('Account suspended'),
        nTime: times.length,
        firstTime: times[0] || null,
        head: t.slice(0, 300)
      }, null, 1);
    })()"""))
finally:
    try:
        proc.terminate()
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)
