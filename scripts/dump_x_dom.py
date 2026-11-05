#!/usr/bin/env python3
"""dump_x_dom.py — dump the DOM structure of X follower/following list for analysis."""
import json
import os
import sys
import time
import tempfile
import shutil
import urllib.request
from pathlib import Path

try:
    import websocket
except ImportError:
    sys.exit("ERROR: 'websocket-client' required")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_locator import resolve_cookie_db
from cdp_launcher import launch, find_free_port, cleanup


class CDP:
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

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def js(page, expression):
    res = page.send("Runtime.evaluate",
                    {"expression": expression, "returnByValue": True,
                     "awaitPromise": True})
    return res.get("result", {}).get("result", {}).get("value")


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
        raise RuntimeError("Could not resolve page websocket")
    page = CDP(page_ws)
    page.send("Network.enable")
    page.send("Page.enable")
    return page


def main():
    cookie_db = resolve_cookie_db("brave", "Default")
    if not cookie_db:
        raise RuntimeError("Cookie DB not found")

    tmp = tempfile.mkdtemp(prefix="reapx_dom_")
    default_dir = os.path.join(tmp, "Default")
    os.makedirs(default_dir, exist_ok=True)
    shutil.copy2(cookie_db, os.path.join(default_dir, "Cookies"))

    port = find_free_port()
    proc, _ = launch("brave", tmp, port)
    print(f"Launched on CDP port {port}")

    browser_cdp = None
    page = None
    try:
        version = wait_for_cdp(port)
        browser_cdp = CDP(version["webSocketDebuggerUrl"])
        page = new_page(browser_cdp, port)

        # Navigate to the target handle's followers page
        page.send("Page.navigate", {"url": f"https://x.com/{sys.argv[1] if len(sys.argv) > 1 else 'target-handle'}/followers"})
        time.sleep(10)

        # Get page info
        title = js(page, "document.title")
        href = js(page, "location.href")
        print(f"Page: {title} @ {href}")

        # Count UserCells
        cell_count = js(page, "document.querySelectorAll('[data-testid=\"UserCell\"]').length")
        print(f"UserCells found: {cell_count}")

        # If no UserCells, dump the page structure
        if not cell_count:
            body_html = js(page, "document.body.innerHTML.substring(0, 5000)")
            print(f"Body HTML:\n{body_html}")

        # Wait more if needed
        if cell_count == 0:
            print("Waiting 10 more seconds...")
            time.sleep(10)
            cell_count = js(page, "document.querySelectorAll('[data-testid=\"UserCell\"]').length")
            print(f"UserCells after wait: {cell_count}")

        # Dump first 3 UserCells
        dump = js(page, """
        (() => {
            const cells = document.querySelectorAll('[data-testid="UserCell"]');
            const out = [];
            for (let i = 0; i < Math.min(3, cells.length); i++) {
                const cell = cells[i];
                out.push({
                    index: i,
                    html: cell.innerHTML.substring(0, 3000),
                    text: cell.innerText,
                    links: Array.from(cell.querySelectorAll('a[href]')).map(a => ({
                        href: a.getAttribute('href'),
                        ariaLabel: a.getAttribute('aria-label'),
                        role: a.getAttribute('role'),
                        text: a.innerText.trim(),
                        spans: Array.from(a.querySelectorAll('span')).map(s => ({
                            dir: s.getAttribute('dir'),
                            text: s.innerText.trim(),
                            className: s.className
                        }))
                    })),
                    spans: Array.from(cell.querySelectorAll('span[dir="auto"]')).map(s => ({
                        text: s.innerText.trim(),
                        className: s.className,
                        parentTag: s.parentElement ? s.parentElement.tagName : null,
                        parentRole: s.parentElement ? s.parentElement.getAttribute('role') : null
                    }))
                });
            }
            return out;
        })()
        """)

        print(json.dumps(dump, indent=2, ensure_ascii=False))

    finally:
        if page:
            page.close()
        if browser_cdp:
            browser_cdp.close()
        cleanup(proc, tmp)


if __name__ == "__main__":
    main()
