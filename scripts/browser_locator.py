#!/usr/bin/env python3
"""browser_locator.py — cross-platform Chromium-family browser detection.

v2 portability layer (ReapX). Detects which Chromium-family browsers are
installed on the current OS and resolves each one's cookie store path.
Browser-agnostic; the only hardcoded knowledge here is the well-known on-disk
layouts each browser uses on macOS / Linux / Windows.

KNOWN BROWSER CONFIG
    name   | macOS app-dir name                | cookie sub-path
    -------+-----------------------------------+------------------------------
    brave  | BraveSoftware/Brave-Browser       | <profile>/Cookies
    chrome | Google/Chrome                     | <profile>/Cookies
    edge   | Microsoft/Edge                    | <profile>/Cookies
    arc    | Arc/User Data                      | <profile>/Cookies
    opera  | Opera Software/Opera Stable       | Cookies

    (Arc's "Opera Stable"-style nesting is handled by its cookie sub-path;
     Chrome on Windows nests under User Data. See COOKIE_SUB_PATHS.)

Values (cookie contents) are NEVER touched by this module — it resolves paths
only.
"""
import os
import platform
from pathlib import Path

# ---------------------------------------------------------------------------
# Known browsers and their on-disk layout per OS.
# ---------------------------------------------------------------------------

# app_dir maps a browser name to the directory (relative to the OS config
# root) that holds its profile directories. For macOS and Linux this is the
# browser's config folder; for Windows it is the "User Data" folder.
APP_DIRS = {
    "brave": "BraveSoftware/Brave-Browser",
    "chrome": "Google/Chrome",
    "edge": "Microsoft/Edge",
    "arc": "Arc/User Data",
    "opera": "Opera Software/Opera Stable",
}

# On Windows the Cookies file lives under a `Network` subfolder of the profile
# and the whole tree sits under `User Data`. On macOS/Linux the profile dir
# holds Cookies directly. Arc on Windows uses the Chrome-style layout.
#   sub-path is appended to:  <config_root>/<app_dir>/<profile>
COOKIE_SUB_PATHS = {
    # default (macOS/Linux): <profile>/Cookies
    "macos": {},
    "linux": {},
    "windows": {
        "brave": "Network/Cookies",
        "chrome": "Network/Cookies",
        "edge": "Network/Cookies",
        "arc": "Network/Cookies",
        "opera": "Cookies",
    },
}

# macOS Keychain service names (used by cookie_store, not here — kept for
# reference and future auto-wiring).
KEYCHAIN_SERVICES = {
    "brave": "Brave Safe Storage",
    "chrome": "Chrome Safe Storage",
    "edge": "Microsoft Edge Safe Storage",
    "arc": "Arc Safe Storage",
    "opera": "Opera Safe Storage",
}

# browser binary / app-bundle candidates used for INSTALL detection.
#   macOS: app bundles in /Applications (and ~/Applications)
#   Linux: names looked up on PATH (which ...)
#   Windows: common install roots
MAC_APP_BUNDLES = {
    "brave": "Brave Browser.app",
    "chrome": "Google Chrome.app",
    "edge": "Microsoft Edge.app",
    "arc": "Arc.app",
    "opera": "Opera.app",
}
LINUX_BINARIES = {
    "brave": ["brave", "brave-browser", "brave-beta"],
    "chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
    "edge": ["microsoft-edge", "microsoft-edge-stable"],
    "arc": ["arc"],  # Arc on Linux is uncommon; keep candidate anyway
    "opera": ["opera", "opera-stable"],
}
WINDOWS_BINARIES = {
    "brave": ["brave.exe"],
    "chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "arc": ["Arc.exe"],
    "opera": ["opera.exe"],
}
WINDOWS_INSTALL_ROOTS = [
    os.environ.get("PROGRAMFILES", r"C:\Program Files"),
    os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    os.environ.get("LOCALAPPDATA", r"C:\Users"),
]
WINDOWS_BINARY_SUBDIRS = {
    "brave": ["BraveSoftware/Brave-Browser/Application", "BraveSoftware/Brave-Browser"],
    "chrome": ["Google/Chrome/Application"],
    "edge": ["Microsoft/Edge/Application", "Microsoft/Edge"],
    "arc": ["Arc/Application", "Arc"],
    "opera": ["Opera Software/Opera Stable", "Opera"],
}

KNOWN_BROWSERS = list(APP_DIRS.keys())


# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
def detect_os():
    """Return 'macos', 'linux', or 'windows' from platform.system()."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return "macos"
    if sysname == "windows":
        return "windows"
    if sysname == "linux":
        return "linux"
    # Fallback: let the caller see the raw value so nothing is silently mapped.
    return sysname


def _config_root(os_name):
    """Return the directory under which <app_dir>/<profile> resolves."""
    home = Path.home()
    if os_name == "macos":
        return home / "Library" / "Application Support"
    if os_name == "linux":
        return home / ".config"
    if os_name == "windows":
        # %LOCALAPPDATA%\<app_dir>\User Data\<profile>
        lap = os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local")
        return Path(lap)
    raise OSError(f"Unsupported OS for config root: {os_name}")


# ---------------------------------------------------------------------------
# Install detection
# ---------------------------------------------------------------------------
def _app_bundle_exists(os_name, browser):
    """macOS: return True if the browser's .app bundle exists."""
    if os_name != "macos":
        return False
    bundle = MAC_APP_BUNDLES.get(browser)
    if not bundle:
        return False
    for base in ("/Applications", str(Path.home() / "Applications")):
        if (Path(base) / bundle).is_dir():
            return True
    return False


def _linux_bin_on_path(browser):
    """Linux: return True if any known binary name resolves on PATH."""
    import shutil
    for name in LINUX_BINARIES.get(browser, []):
        if shutil.which(name):
            return True
    return False


def _windows_bin_exists(browser):
    """Windows: return True if any known install-root/binary path exists."""
    for root in WINDOWS_INSTALL_ROOTS:
        for sub in WINDOWS_BINARY_SUBDIRS.get(browser, []):
            for exe in WINDOWS_BINARIES.get(browser, []):
                p = Path(root) / sub / exe
                if p.is_file():
                    return True
    return False


def is_installed(os_name, browser):
    """Return True if the given browser is installed on the given OS."""
    if browser not in APP_DIRS:
        return False
    if os_name == "macos":
        return _app_bundle_exists(os_name, browser)
    if os_name == "linux":
        return _linux_bin_on_path(browser)
    if os_name == "windows":
        return _windows_bin_exists(browser)
    return False


def get_installed_browsers():
    """Return a list of installed browser names (in KNOWN_BROWSERS order)."""
    os_name = detect_os()
    return [b for b in KNOWN_BROWSERS if is_installed(os_name, b)]


# ---------------------------------------------------------------------------
# Cookie DB path resolution
# ---------------------------------------------------------------------------
def resolve_cookie_db(browser, profile="Default"):
    """Return the absolute path to a browser's Cookies SQLite DB.

    Returns None if the browser is unknown or its profile directory does not
    exist on this machine. Never touches cookie values.
    """
    os_name = detect_os()
    app_dir = APP_DIRS.get(browser)
    if not app_dir:
        return None
    root = _config_root(os_name)
    profile_dir = root / app_dir / profile
    sub = COOKIE_SUB_PATHS.get(os_name, {}).get(browser, "Cookies")
    candidate = profile_dir / sub
    if candidate.is_file():
        return str(candidate)
    return None


if __name__ == "__main__":
    # Standalone smoke: print detection info.
    os_name = detect_os()
    print(f"OS: {os_name}")
    installed = get_installed_browsers()
    print(f"Installed browsers: {installed or 'none detected'}")
    for b in installed:
        print(f"  {b}: {resolve_cookie_db(b)}")
