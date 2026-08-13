#!/usr/bin/env python3
"""test_cookie_store.py — unit tests for the ReapX v2 portability layer.

Covers:
  1. cookie_store.decrypt_value against a KNOWN v10 test vector (generated
     in-test with a known key + deterministic PKCS7 padding), and the openssl
     fallback path when `cryptography` is missing.
  2. browser_locator detects brave on this macOS machine.
  3. browser_locator.resolve_cookie_db returns the real Brave cookie path.
  4. cookie_store.keychain service map is sane (brave -> Brave Safe Storage).

Prints PASS/FAIL per test; exits 0 only if every test passes.
Never touches or prints real cookie values.
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


def test_detect_os_maps_known():
    # OS-portable: detect_os() must return one of the three supported roots.
    os_name = detect_os()
    check(
        "detect_os() returns a supported OS root",
        os_name in ("macos", "linux", "windows"),
        f"got {os_name!r}")


def test_brave_installed():
    os_name = detect_os()
    if os_name == "macos":
        check(
            "brave detected as installed on macOS",
            is_installed(os_name, "brave"),
            "Brave Browser.app not found in /Applications or ~/Applications")
    else:
        # OS-portable: is_installed must return a bool without raising.
        check(
            f"is_installed('brave') returns bool on {os_name}",
            isinstance(is_installed(os_name, "brave"), bool),
            "is_installed must not raise on non-macOS")


def test_brave_in_installed_list():
    installed = get_installed_browsers()
    if detect_os() == "macos":
        check("brave in get_installed_browsers()", "brave" in installed, f"got {installed}")
    else:
        # OS-portable: returns a list of known browser names.
        check(
            "get_installed_browsers() returns a list",
            isinstance(installed, list) and all(b in KEYCHAIN_SERVICES for b in installed),
            f"got {installed!r}")


def test_resolve_cookie_db_brave():
    path = resolve_cookie_db("brave")
    if detect_os() == "macos":
        expected = (
            Path.home() / "Library" / "Application Support"
            / "BraveSoftware" / "Brave-Browser" / "Default" / "Cookies"
        )
        check(
            "resolve_cookie_db('brave') returns real Brave cookie path",
            path == str(expected) and os.path.isfile(path),
            f"got {path!r}")
    else:
        # OS-portable: must return a path string or None, never raise.
        check(
            "resolve_cookie_db('brave') returns str or None",
            path is None or isinstance(path, str),
            f"got {path!r}")


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
    test_brave_installed()
    test_brave_in_installed_list()
    test_resolve_cookie_db_brave()
    test_unknown_browser_returns_none()
    test_keychain_service_map()
    print()
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(0 if not FAIL else 1)


if __name__ == "__main__":
    main()
