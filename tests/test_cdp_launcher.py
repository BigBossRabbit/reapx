#!/usr/bin/env python3
"""test_reapx_cdp_launcher.py — unit tests for the ReapX v2 CDP launcher.

Covers (all OS-portable — no live browser session, no real login):
  1. find_free_port() returns an int > 0 that is actually bindable.
  2. locate_browser_binary('brave') is GUARDED: it must resolve to a real
     executable only when Brave is installed, otherwise it SKIPs. The real
     portable assertion is locate_browser_binary(first installed browser) —
     an installed browser always has an executable on any OS (dev Mac, CI
     macos/ubuntu/windows).
  3. locate_browser_binary on an unknown browser returns None.
  4. cleanup() tolerates a finished/dead process and a missing profile dir
     (no pkill anywhere).

Prints PASS/SKIP/FAIL per test; exits 0 only if every (non-skipped) test
passes.
"""
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import reapx_cdp_launcher
from reapx_browser_locator import detect_os, get_installed_browsers, is_installed
from reapx_cdp_launcher import (find_free_port, locate_browser_binary,
                                cleanup, launch, minimal_env)

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


def test_locate_browser_binary_brave_guarded():
    # Brave is the reference install on the dev machine but NOT on CI.
    # Guard the brave-specific check: run only when Brave is installed,
    # otherwise SKIP (portable across every machine).
    if not is_installed(detect_os(), "brave"):
        print("SKIP: locate_browser_binary('brave') — brave not installed")
        return
    path = locate_browser_binary("brave")
    check(
        "locate_browser_binary('brave') returns a real executable (installed)",
        isinstance(path, str) and Path(path).is_file(),
        f"got {path!r}")


def test_locate_first_installed_browser():
    # Portable: the auto/first-installed browser must always resolve to a real
    # executable on ANY machine (dev Mac, CI macos/ubuntu/windows). An installed
    # browser is guaranteed to have an executable, so this is a hard assertion —
    # no specific browser is assumed.
    installed = get_installed_browsers()
    if not installed:
        print("SKIP: locate first installed browser — no browser detected")
        return
    first = installed[0]
    path = locate_browser_binary(first)
    check(
        f"locate_browser_binary({first!r} [first installed]) returns a real executable",
        isinstance(path, str) and Path(path).is_file(),
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


def _capture_launch(port=9333):
    """Run launch() against a fake binary + fake Popen; return (cmd, kwargs)."""
    calls = {}
    real_locate = reapx_cdp_launcher.locate_browser_binary
    real_popen = reapx_cdp_launcher.subprocess.Popen

    class FakeProc:
        pass

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = list(cmd)
        calls["kwargs"] = dict(kwargs)
        return FakeProc()

    reapx_cdp_launcher.locate_browser_binary = lambda _b: "/bin/true"
    reapx_cdp_launcher.subprocess.Popen = fake_popen
    try:
        proc, got_port = launch("brave", "/tmp/reapx_test_profile", port)
    finally:
        reapx_cdp_launcher.locate_browser_binary = real_locate
        reapx_cdp_launcher.subprocess.Popen = real_popen
    return calls["cmd"], calls["kwargs"], got_port


def test_allow_origins_is_the_loopback_origin_only():
    """Review point 2: no --remote-allow-origins=* — exactly one origin,
    the loopback at the actual port."""
    cmd, _, port = _capture_launch(9333)
    origins = [c for c in cmd if c.startswith("--remote-allow-origins")]
    check("exactly one --remote-allow-origins flag", len(origins) == 1,
          f"got {origins!r}")
    check("origin is http://127.0.0.1:<port>, not *",
          origins == [f"--remote-allow-origins=http://127.0.0.1:{port}"],
          f"got {origins!r}")
    check("the port in the origin is the debug port",
          f"--remote-debugging-port={port}" in cmd,
          f"cmd={cmd!r}")
    check("no bare * anywhere in the flags",
          "--remote-allow-origins=*" not in cmd)


def test_minimal_env_never_inherits_the_parent():
    """Review point 4: the child gets an explicit env, not the Hermes env
    (which carries every provider key)."""
    os.environ["REAPX_TEST_PROVIDER_KEY"] = "sk-not-a-real-key"
    try:
        _, kwargs, _ = _capture_launch(9334)
    finally:
        os.environ.pop("REAPX_TEST_PROVIDER_KEY", None)
    env = kwargs.get("env")
    check("launch passes an explicit env=", isinstance(env, dict),
          f"got {env!r}")
    check("the sentinel provider key is NOT inherited",
          "REAPX_TEST_PROVIDER_KEY" not in (env or {}))
    check("every env key is on the minimal allowlist",
          set(env or {}) <= set(reapx_cdp_launcher.ENV_KEYS),
          f"got {sorted(env or {})}")
    check("PATH survives when the parent has it",
          ("PATH" not in os.environ) or ("PATH" in (env or {})))
    check("HOME survives when the parent has it",
          ("HOME" not in os.environ) or ("HOME" in (env or {})))
    check("the built env is minimal_env()",
          minimal_env() == env, "minimal_env() and the launch env differ")


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
    test_locate_browser_binary_brave_guarded()
    test_locate_first_installed_browser()
    test_locate_unknown_browser_none()
    test_cleanup_dead_proc_and_missing_dir()
    test_cleanup_removes_profile_when_keep_false()
    test_allow_origins_is_the_loopback_origin_only()
    test_minimal_env_never_inherits_the_parent()
    test_cleanup_keeps_profile_when_keep_true()
    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
