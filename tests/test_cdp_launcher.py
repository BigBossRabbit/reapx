#!/usr/bin/env python3
"""test_cdp_launcher.py — unit tests for the ReapX v2 CDP launcher.

Covers (all OS-portable — no live browser session, no real login):
  1. find_free_port() returns an int > 0 that is actually bindable.
  2. locate_browser_binary('brave') returns a real path on macOS (where Brave
     is the reference install); on Linux/Windows CI it must return a valid
     path, or None without raising (portable guard by OS).
  3. locate_browser_binary on an unknown browser returns None.
  4. cleanup() tolerates a finished/dead process and a missing profile dir
     (no pkill anywhere).

Prints PASS/FAIL per test; exits 0 only if every test passes.
"""
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from browser_locator import detect_os
from cdp_launcher import find_free_port, locate_browser_binary, cleanup

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS: {name}")
    else:
        FAIL.append(name)
        print(f"FAIL: {name}  {detail}")


def test_find_free_port_positive_int():
    port = find_free_port()
    check(
        "find_free_port returns an int > 0",
        isinstance(port, int) and port > 0,
        f"got {port!r}")


def test_find_free_port_bindable():
    port = find_free_port()
    bindable = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", port))
            bindable = True
    except OSError:
        bindable = False
    check("find_free_port port is bindable", bindable, f"port={port}")


def test_locate_browser_binary_brave():
    os_name = detect_os()
    path = locate_browser_binary("brave")
    if os_name == "macos":
        # Reference machine: Brave is installed, must resolve to a real binary.
        check(
            "locate_browser_binary('brave') returns a real path on macOS",
            isinstance(path, str) and Path(path).is_file(),
            f"got {path!r}")
    else:
        # Portable on CI (Linux/Windows): either a real path, or None if Brave
        # is not installed there — never an exception.
        ok = path is None or (isinstance(path, str) and Path(path).is_file())
        check(
            f"locate_browser_binary('brave') is a path or None on {os_name}",
            ok,
            f"got {path!r}")


def test_locate_unknown_browser_none():
    check(
        "locate_browser_binary('nonexistent') is None",
        locate_browser_binary("nonexistent") is None,
        f"got {locate_browser_binary('nonexistent')!r}")


def test_cleanup_dead_proc_and_missing_dir():
    # Simulate a dead process object + a path that may not exist; cleanup must
    # not raise (cross-platform, no pkill).
    class FakeProc:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True
            raise ProcessLookupError  # process already gone

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            raise ProcessLookupError

    fp = FakeProc()
    ok = True
    detail = ""
    try:
        cleanup(fp, "/nonexistent/reapx_profile_dir", keep=False)
    except Exception as e:
        ok = False
        detail = f"raised {e!r}"
    check(
        "cleanup tolerates dead process + missing dir",
        ok,
        detail if not ok else "no exception")


def test_cleanup_removes_profile_when_keep_false():
    tmp = tempfile.mkdtemp(prefix="reapx_cleanup_")
    (Path(tmp) / "Default").mkdir()
    try:
        cleanup(None, tmp, keep=False)
        check("cleanup removes profile dir when keep=False",
              not Path(tmp).exists(),
              f"dir still present: {tmp}")
    finally:
        # Ensure the temp dir is gone even if the assertion failed.
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_cleanup_keeps_profile_when_keep_true():
    tmp = tempfile.mkdtemp(prefix="reapx_cleanup_keep_")
    try:
        cleanup(None, tmp, keep=True)
        check("cleanup keeps profile dir when keep=True", Path(tmp).exists())
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_find_free_port_positive_int()
    test_find_free_port_bindable()
    test_locate_browser_binary_brave()
    test_locate_unknown_browser_none()
    test_cleanup_dead_proc_and_missing_dir()
    test_cleanup_removes_profile_when_keep_false()
    test_cleanup_keeps_profile_when_keep_true()
    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
