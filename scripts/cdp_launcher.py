#!/usr/bin/env python3
"""cdp_launcher.py — cross-platform Chromium-family CDP launcher (v2, ROADMAP layer 3).

Decouples "how the browser process is started and stopped" from the harvester,
so ReapX works on macOS / Linux / Windows with any Chromium-family browser:

    locate_browser_binary(browser)  -> absolute path to the browser executable (or None)
    find_free_port()                -> an ephemeral, bindable TCP port (never the
                                       hardcoded 9222, avoiding the collision v1 had)
    launch(browser, profile_dir, port) -> Popen headless over CDP; returns (proc, port)
    cleanup(proc, profile_dir, keep)   -> terminate + wait + kill fallback (NO pkill),
                                          optionally remove the temp profile

Binary-path knowledge lives here (imported tables from browser_locator); cookie
paths live in browser_locator; cookie values are NEVER touched by this module.
"""
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from browser_locator import (
    detect_os,
    MAC_APP_BUNDLES,
    LINUX_BINARIES,
    WINDOWS_BINARIES,
    WINDOWS_INSTALL_ROOTS,
    WINDOWS_BINARY_SUBDIRS,
)

# ---------------------------------------------------------------------------
# Browser executable discovery
# ---------------------------------------------------------------------------
def _macos_binary(browser):
    """Return the executable path inside a browser's .app bundle, or None."""
    bundle = MAC_APP_BUNDLES.get(browser)
    if not bundle:
        return None
    exe_name = bundle[: -len(".app")] if bundle.endswith(".app") else bundle
    for base in ("/Applications", str(Path.home() / "Applications")):
        macos_dir = Path(base) / bundle / "Contents" / "MacOS"
        if not macos_dir.is_dir():
            continue
        # Preferred: the executable shares the bundle name (Brave Browser,
        # Google Chrome, Microsoft Edge, Opera, Arc ...).
        cand = macos_dir / exe_name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
        # Fallback: any executable regular file inside Contents/MacOS.
        for p in macos_dir.iterdir():
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
    return None


def _linux_binary(browser):
    """Return the first browser binary found on PATH, or None."""
    for name in LINUX_BINARIES.get(browser, []):
        p = shutil.which(name)
        if p:
            return p
    return None


def _windows_binary(browser):
    """Return the first browser binary found under common install roots, or None."""
    for root in WINDOWS_INSTALL_ROOTS:
        for sub in WINDOWS_BINARY_SUBDIRS.get(browser, []):
            for exe in WINDOWS_BINARIES.get(browser, []):
                p = Path(root) / sub / exe
                if p.is_file():
                    return str(p)
    return None


def locate_browser_binary(browser):
    """Return the absolute path to a browser's executable, or None if not found.

    Works per-OS:
        macOS   /Applications/X.app/Contents/MacOS/X
        Linux   `which brave-browser / google-chrome / microsoft-edge ...`
        Windows %ProgramFiles% / %ProgramFiles(x86)% install roots
    """
    os_name = detect_os()
    if os_name == "macos":
        return _macos_binary(browser)
    if os_name == "linux":
        return _linux_binary(browser)
    if os_name == "windows":
        return _windows_binary(browser)
    return None


# ---------------------------------------------------------------------------
# Port allocation (avoid the hardcoded 9222 collision)
# ---------------------------------------------------------------------------
def find_free_port():
    """Return an ephemeral, currently-bindable TCP port as an int.

    Binds a socket to port 0, reads the OS-assigned port, closes it, and
    returns that number. There is an inherent TOCTOU window (the port is free
    again after close), so launch() must be called promptly; this is exactly
    how the OS hands out ephemeral ports and is far better than pinning 9222.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        port = s.getsockname()[1]
    return int(port)


# ---------------------------------------------------------------------------
# Launch / cleanup
# ---------------------------------------------------------------------------
def launch(browser, profile_dir, port, headless=True, extra_flags=None):
    """Launch the browser headless over CDP on `port` with `profile_dir`.

    Returns (proc, port). Callers must wait for the CDP endpoint (the /json/
    HTTP API) to come up before opening a websocket — that wait lives in the
    caller so the readiness probe can stay next to the CDP client.

    Args:
        browser: browser name ('brave'|'chrome'|'edge'|'arc'|'opera')
        profile_dir: existing user-data-dir to launch against (holds the
                     copied Cookies DB for session injection)
        port: the CDP debug port (use find_free_port() when auto)
        headless: pass --headless=new
        extra_flags: optional list of extra Chromium switches

    Raises RuntimeError if the browser binary cannot be located.
    """
    binary = locate_browser_binary(browser)
    if not binary:
        raise RuntimeError(
            f"No '{browser}' executable found on this OS "
            f"({detect_os()}); run scripts/locate_browser.py to see what is installed")

    cmd = [
        binary,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--window-size=1280,2400",
        "--remote-allow-origins=*",
    ]
    if headless:
        cmd.insert(1, "--headless=new")
    if extra_flags:
        cmd.extend(extra_flags)
    cmd.append("about:blank")

    # On Windows, keep the child from popping a console window.
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    return proc, port


def cleanup(proc, profile_dir, keep=False):
    """Terminate the browser process and optionally remove the temp profile.

    Cross-platform: uses proc.terminate() + wait + kill fallback — NEVER pkill
    (pkill is Unix-only and namespaced-fragile). If `keep` is True the temp
    profile directory is left in place for debugging.
    """
    if proc is not None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        except Exception:
            pass  # process already gone — nothing to do

    if profile_dir and not keep:
        profile = Path(profile_dir)
        if profile.is_dir():
            shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    os_name = detect_os()
    print(f"OS: {os_name}")
    for b in ["brave", "chrome", "edge", "arc", "opera"]:
        print(f"  {b:<6} -> {locate_browser_binary(b) or '(not found)'}")
    print(f"free port: {find_free_port()}")
