#!/usr/bin/env python3
"""test_config.py — unit tests for the ReapX v2 config layer (reapx_config).

Covers:
  1. --browser chrome / edge explicit flags are honored.
  2. auto resolution: load_config --browser auto returns a known browser name
     (first installed, per browser_locator) — portable across OSes.
  3. Defaults: profile 'Default', port 0 (auto-free), output data/x_bookmarks.json.
  4. CLI overrides: --output, --keep-profile, --max-scrolls, --port.
  5. Env var fallbacks (REAPX_BROWSER, REAPX_OUTPUT) when no CLI flag given.

Prints PASS/FAIL per test; exits 0 only if every test passes.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from browser_locator import KNOWN_BROWSERS, get_installed_browsers
from reapx_config import load_config, auto_resolve_browser, DEFAULT_OUTPUT

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS: {name}")
    else:
        FAIL.append(name)
        print(f"FAIL: {name}  {detail}")


def test_browser_flag_chrome():
    cfg = load_config(["--browser", "chrome"])
    check("--browser chrome parsed", cfg.browser == "chrome", f"got {cfg.browser!r}")


def test_browser_flag_edge():
    cfg = load_config(["--browser", "edge"])
    check("--browser edge parsed", cfg.browser == "edge", f"got {cfg.browser!r}")


def test_auto_resolves_to_known_browser():
    cfg = load_config(["--browser", "auto"])
    check(
        "auto resolves to a known browser name",
        cfg.browser in KNOWN_BROWSERS,
        f"got {cfg.browser!r}")


def test_auto_resolve_browser_matches_installed():
    resolved = auto_resolve_browser()
    installed = get_installed_browsers()
    # When any browser is installed, auto must return the first installed one.
    if installed:
        check(
            "auto_resolve_browser() == first installed",
            resolved == installed[0],
            f"resolved={resolved!r} installed={installed!r}")
    else:
        # No browsers detected: safe fallback to 'brave' (downstream surfaces
        # the real error). Portable across OSes.
        check(
            "auto_resolve_browser() falls back to a known browser when none installed",
            resolved in KNOWN_BROWSERS,
            f"got {resolved!r}")


def test_default_profile():
    cfg = load_config(["--browser", "chrome"])
    check("default profile == 'Default'", cfg.profile == "Default", f"got {cfg.profile!r}")


def test_default_port_auto_free():
    cfg = load_config(["--browser", "chrome"])
    check("default port == 0 (auto-free)", cfg.port == 0, f"got {cfg.port!r}")


def test_default_output():
    cfg = load_config(["--browser", "chrome"])
    check(
        "default output == data/x_bookmarks.json",
        cfg.output == DEFAULT_OUTPUT,
        f"got {cfg.output!r}")


def test_output_flag_override():
    cfg = load_config(["--browser", "chrome", "--output", "/tmp/custom.json"])
    check(
        "--output overrides default",
        cfg.output == Path("/tmp/custom.json"),
        f"got {cfg.output!r}")


def test_port_flag_override():
    cfg = load_config(["--browser", "chrome", "--port", "9333"])
    check("--port 9333 parsed", cfg.port == 9333, f"got {cfg.port!r}")


def test_keep_profile_flag():
    cfg = load_config(["--browser", "chrome", "--keep-profile"])
    check("--keep-profile defaults to True when passed", cfg.keep_profile is True)


def test_keep_profile_default_false():
    cfg = load_config(["--browser", "chrome"])
    check("--keep-profile defaults to False when omitted", cfg.keep_profile is False)


def test_max_scrolls_flag():
    cfg = load_config(["--browser", "chrome", "--max-scrolls", "10"])
    check("--max-scrolls 10 parsed", cfg.max_scrolls == 10, f"got {cfg.max_scrolls!r}")


def test_max_scrolls_default():
    cfg = load_config(["--browser", "chrome"])
    check(
        "--max-scrolls defaults to 500",
        cfg.max_scrolls == 500,
        f"got {cfg.max_scrolls!r}")


def test_env_browser_fallback():
    old = os.environ.get("REAPX_BROWSER")
    os.environ["REAPX_BROWSER"] = "chrome"
    try:
        cfg = load_config([])
        check("REAPX_BROWSER env used when no flag", cfg.browser == "chrome",
              f"got {cfg.browser!r}")
    finally:
        if old is None:
            os.environ.pop("REAPX_BROWSER", None)
        else:
            os.environ["REAPX_BROWSER"] = old


def test_env_output_fallback():
    old = os.environ.get("REAPX_OUTPUT")
    os.environ["REAPX_OUTPUT"] = "/tmp/env_out.json"
    try:
        cfg = load_config(["--browser", "chrome"])
        check("REAPX_OUTPUT env used when no flag",
              cfg.output == Path("/tmp/env_out.json"),
              f"got {cfg.output!r}")
    finally:
        if old is None:
            os.environ.pop("REAPX_OUTPUT", None)
        else:
            os.environ["REAPX_OUTPUT"] = old


def main():
    test_browser_flag_chrome()
    test_browser_flag_edge()
    test_auto_resolves_to_known_browser()
    test_auto_resolve_browser_matches_installed()
    test_default_profile()
    test_default_port_auto_free()
    test_default_output()
    test_output_flag_override()
    test_port_flag_override()
    test_keep_profile_flag()
    test_keep_profile_default_false()
    test_max_scrolls_flag()
    test_max_scrolls_default()
    test_env_browser_fallback()
    test_env_output_fallback()
    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
