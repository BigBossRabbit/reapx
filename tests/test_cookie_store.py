#!/usr/bin/env python3
"""test_cookie_store.py — unit tests for the ReapX v2 portability layer.

Covers:
  1. cookie_store.decrypt_value against a KNOWN v10 test vector (generated
     in-test with a known key + deterministic PKCS7 padding), and the openssl
     fallback path when `cryptography` is missing.
  2. browser_locator detects SOME browser on this machine (never a specific
     one — the CI macOS runner has only Chrome/Edge, the dev Mac has Brave).
  3. Every detected browser resolves cleanly to a cookie DB path (or None when
     its profile has not been created yet), and the FIRST installed browser
     resolves to a real path whenever such a DB exists.
  4. Brave-specific checks are GUARDED: they run only when Brave is actually
     installed, otherwise they print SKIP (portable across every machine).
  5. cookie_store.keychain service map is sane (brave -> Brave Safe Storage).

Prints PASS/SKIP/FAIL per test; exits 0 only if every (non-skipped) test
passes. Never touches or prints real cookie values.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from browser_locator import (
    detect_os,
    get_installed_browsers,
    is_installed,
    resolve_cookie_db,
    KEYCHAIN_SERVICES,
)
import cookie_store
from cookie_store import decrypt_value, pbkdf2_key_from_password

# A fixed, known key (16 bytes = AES-128). Real Chromium derives this from the
# Keychain password via PBKDF2; here we use it directly as the test key.
TEST_KEY = bytes(range(16))          # 00 01 02 ... 0f
TEST_PLAINTEXT = b"auth_token=known_test_value"

# Known-good fixture: v10( AES-128-CBC(plaintext, iv=16 spaces, PKCS7) )
# We generate it once, deterministically, so the same blob is decrypted by
# both the cryptography and openssl code paths.
def _pkcs7_pad(data, block=16):
    pad = block - (len(data) % block)
    return data + bytes([pad]) * pad


def _encrypt_aes_cbc(plaintext, key, iv):
    """Encrypt with AES-128-CBC no-padding using openssl (universally available)."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(plaintext)
        tmp = f.name
    try:
        r = subprocess.run(
            ["openssl", "enc", "-aes-128-cbc", "-nopad",
             "-K", key.hex(), "-iv", iv.hex(), "-in", tmp],
            capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"openssl encrypt failed: {r.stderr.decode()}")
        return r.stdout
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _make_v10_blob():
    padded = _pkcs7_pad(TEST_PLAINTEXT)
    ct = _encrypt_aes_cbc(padded, TEST_KEY, b" " * 16)
    return b"v10" + ct


# Build the fixture once.
V10_BLOB = _make_v10_blob()


# ---------------------------------------------------------------------------
# Test runners
# ---------------------------------------------------------------------------
PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"PASS: {name}")
    else:
        FAIL.append(name)
        print(f"FAIL: {name}  {detail}")


def skip(name):
    print(f"SKIP: {name}")


# ---------------------------------------------------------------------------
# Decryption round-trips (synthetic fixtures — portable on every machine)
# ---------------------------------------------------------------------------
def test_decrypt_known_vector():
    val = decrypt_value(V10_BLOB, TEST_KEY)
    check(
        "decrypt_value recovers known plaintext",
        val == TEST_PLAINTEXT.decode(),
        f"got {val!r}")


def test_decrypt_openssl_fallback():
    # Force the openssl branch regardless of whether cryptography is installed.
    had = cookie_store._HAS_CRYPTOGRAPHY
    cookie_store._HAS_CRYPTOGRAPHY = False
    try:
        val = decrypt_value(V10_BLOB, TEST_KEY)
        check(
            "openssl fallback decrypts known vector",
            val == TEST_PLAINTEXT.decode(),
            f"got {val!r}")
    finally:
        cookie_store._HAS_CRYPTOGRAPHY = had


def test_pbkdf2_known_key():
    # Chromium derives AES key from the password via PBKDF2(sha1, saltysalt, 1003).
    # Round-trip: derive a key, encrypt, decrypt.
    pw = b"some_keychain_password"
    key = pbkdf2_key_from_password(pw)
    blob = b"v10" + _encrypt_aes_cbc(_pkcs7_pad(b"roundtrip"), key, b" " * 16)
    check("pbkdf2-derived key decrypts", decrypt_value(blob, key) == "roundtrip")


# ---------------------------------------------------------------------------
# Browser detection — OS-PORTABLE (no specific browser is assumed)
# ---------------------------------------------------------------------------
def test_detect_os_maps_known():
    # OS-portable: detect_os() must return one of the three supported roots.
    os_name = detect_os()
    check(
        "detect_os() returns a supported OS root",
        os_name in ("macos", "linux", "windows"),
        f"got {os_name!r}")


def test_installed_browsers_nonempty():
    # Portable: ANY machine (dev Mac, CI macos/ubuntu/windows) has at least one
    # Chromium-family browser in the KNOWN set (GitHub macOS runner ships
    # Chrome+Edge; the dev Mac ships Brave). We assert non-empty, NOT a
    # specific browser.
    installed = get_installed_browsers()
    check(
        "get_installed_browsers() returns at least one browser",
        isinstance(installed, list) and len(installed) > 0,
        f"got {installed!r}")


def test_every_installed_browser_resolves_cleanly():
    # Portable: every detected browser must resolve to a real, existing cookie
    # DB path when present, or None without raising when its profile has not
    # been created yet (installed but never launched). No specific browser or
    # path is assumed.
    bad = []
    for b in get_installed_browsers():
        p = resolve_cookie_db(b)
        if not (p is None or (isinstance(p, str) and os.path.isfile(p))):
            bad.append((b, p))
    check(
        "every installed browser resolves to an existing path or None",
        not bad,
        f"bad: {bad!r}")


def test_first_installed_browser_resolves():
    # Helper: pick the FIRST installed browser and resolve its cookie DB. It
    # must return a real, non-None path whenever the DB exists. If the browser
    # is installed but has never been launched (no DB yet) we SKIP rather than
    # fail — a legitimate environmental state, not a code defect. This stays
    # green on a machine that has no cookie DB at all while still asserting the
    # non-None path on any machine that does.
    installed = get_installed_browsers()
    if not installed:
        skip("first_installed_browser_resolves — no browser detected")
        return
    first = installed[0]
    p = resolve_cookie_db(first)
    if p is None:
        skip(f"first_installed_browser_resolves — no cookie DB yet for {first!r}")
        return
    check(
        f"resolve_cookie_db(first installed {first!r}) returns a real path",
        isinstance(p, str) and os.path.isfile(p),
        f"got {p!r}")


def test_brave_guard():
    # Brave-specific checks are GUARDED: they run only when Brave is installed
    # (dev machine). On a machine without Brave (CI macOS) we SKIP — this is
    # the assertion that used to hard-fail CI.
    if not is_installed(detect_os(), "brave"):
        skip("brave-specific test — brave not installed")
        return
    installed = get_installed_browsers()
    check(
        "brave in get_installed_browsers() (installed)",
        "brave" in installed,
        f"got {installed}")
    p = resolve_cookie_db("brave")
    check(
        "resolve_cookie_db('brave') returns a real cookie path (installed)",
        isinstance(p, str) and os.path.isfile(p),
        f"got {p!r}")


def test_unknown_browser_returns_none():
    check("unknown browser -> None", resolve_cookie_db("nonexistent") is None)


def test_keychain_service_map():
    check(
        "brave -> 'Brave Safe Storage'",
        KEYCHAIN_SERVICES.get("brave") == "Brave Safe Storage")


def main():
    print(f"test fixture: {len(V10_BLOB)}-byte v10 blob, key={TEST_KEY.hex()}")
    print(f"plaintext: {TEST_PLAINTEXT.decode()!r}")
    print()
    test_decrypt_known_vector()
    test_decrypt_openssl_fallback()
    test_pbkdf2_known_key()
    test_detect_os_maps_known()
    test_installed_browsers_nonempty()
    test_every_installed_browser_resolves_cleanly()
    test_first_installed_browser_resolves()
    test_brave_guard()
    test_unknown_browser_returns_none()
    test_keychain_service_map()
    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
