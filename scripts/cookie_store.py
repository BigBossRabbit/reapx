#!/usr/bin/env python3
"""cookie_store.py — cross-platform Chromium cookie decryption (v2).

One interface, per-OS key resolution:
    read_cookies(browser, profile='Default') -> {name: value}

The encryption scheme is Chromium's v10/v11 AES-128-CBC (the same for every
Chromium-family browser); only the KEY SOURCE differs per OS:
    macOS   -> Keychain via `security find-generic-password`
    Linux   -> libsecret via `secret-tool` (falls back to the Local State
               os_crypt.encrypted_key plaintext for older Chromium)
    Windows -> DPAPI via win32crypt (guarded import)

Cookie VALUES are returned in memory only and are never printed, logged, or
persisted. This module does not require the `cryptography` package — it uses
it when available and falls back to an `openssl` subprocess otherwise.

Decryption helpers mirror fetch_x_bookmarks.py's proven v10/v11 logic.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from browser_locator import (
    detect_os,
    KEYCHAIN_SERVICES,
    resolve_cookie_db,
)

# ---------------------------------------------------------------------------
# Decryption primitives (reused from fetch_x_bookmarks.py v1)
# ---------------------------------------------------------------------------

# Try the `cryptography` package first; fall back to an openssl subprocess.
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAS_CRYPTOGRAPHY = True
except ImportError:
    _HAS_CRYPTOGRAPHY = False


def _aes_cbc_decrypt(ct, key, iv):
    """AES-128-CBC no-padding decrypt of ct (bytes) -> bytes or None."""
    if _HAS_CRYPTOGRAPHY:
        try:
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            dec = cipher.decryptor()
            return dec.update(ct) + dec.finalize()
        except Exception:
            return None
    # openssl fallback (v1 used exactly this).
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(ct)
        tmp = f.name
    try:
        r = subprocess.run(
            ["openssl", "enc", "-d", "-aes-128-cbc", "-nopad",
             "-K", key.hex(), "-iv", iv.hex(), "-in", tmp],
            capture_output=True)
        return r.stdout if r.returncode == 0 else None
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def pbkdf2_key_from_password(password_bytes):
    """Chromium's key derivation: PBKDF2-HMAC-SHA1, 'saltysalt', 1003 iters, 16 bytes."""
    return hashlib.pbkdf2_hmac("sha1", password_bytes, b"saltysalt", 1003, 16)


def decrypt_value(encrypted_value, key):
    """Decrypt a v10/v11 Chromium encrypted cookie value. Returns str or None."""
    if not encrypted_value or encrypted_value[:3] not in (b"v10", b"v11"):
        return None
    raw = _aes_cbc_decrypt(encrypted_value[3:], key, b" " * 16)
    if not raw:
        return None
    pad = raw[-1]
    if 1 <= pad <= 16 and pad <= len(raw):
        raw = raw[:-pad]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Key resolution per OS
# ---------------------------------------------------------------------------
def resolve_key_macos(browser="brave"):
    """Return the AES key from the macOS Keychain for the browser's service."""
    service = KEYCHAIN_SERVICES.get(browser, KEYCHAIN_SERVICES["brave"])
    r = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", service],
        capture_output=True, text=True, check=True)
    return pbkdf2_key_from_password(r.stdout.rstrip("\n").encode())


def _local_state_path(browser, profile):
    """Path to the Chromium 'Local State' JSON for a browser (macOS/Linux)."""
    os_name = detect_os()
    if os_name == "macos":
        base = Path.home() / "Library" / "Application Support"
    elif os_name == "linux":
        base = Path.home() / ".config"
    else:
        return None
    from browser_locator import APP_DIRS
    app_dir = APP_DIRS.get(browser)
    if not app_dir:
        return None
    # Local State sits at the config root, not inside a profile.
    return base / app_dir / "Local State"


def resolve_key_linux(browser="chrome", profile="Default"):
    """Linux: keyring first (secret-tool/libsecret), else Local State plaintext.

    Modern Chromium Linux stores the AES key in the keyring (org.chromium.
    Chromium) wrapped by the plaintext key from os_crypt.encrypted_key in Local
    State. When secret-tool is unavailable, we return the Local State key,
    which decrypts cookies on distros that keep it plaintext.
    """
    # 1) Try libsecret via secret-tool.
    secret_tool = subprocess.run(["which", "secret-tool"], capture_output=True, text=True)
    if secret_tool.returncode == 0:
        try:
            r = subprocess.run(
                ["secret-tool", "search", "--all", "application", "chromium"],
                capture_output=True, text=True)
            # Simple best-effort: secret-tool doesn't expose -w; parse "secret = ..."
            secret = None
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("secret") and "=" in line:
                    secret = line.split("=", 1)[1].strip()
                    break
            if secret:
                return pbkdf2_key_from_password(secret.encode())
        except Exception:
            pass

    # 2) Local State os_crypt.encrypted_key (plaintext on Linux).
    ls = _local_state_path(browser, profile)
    if ls and ls.is_file():
        try:
            data = json.loads(ls.read_text(encoding="utf-8"))
            b64key = data.get("os_crypt", {}).get("encrypted_key")
            if b64key:
                import base64
                raw = base64.b64decode(b64key)
                # Linux: no DPAPI prefix — the key is already plaintext.
                if raw.startswith(b"DPAPI"):
                    raw = raw[5:]
                return raw
        except Exception:
            pass
    raise RuntimeError(
        f"No Linux cookie key available for '{browser}' "
        "(secret-tool missing and no usable Local State key)")


def resolve_key_windows(browser="chrome", profile="Default"):
    """Windows: DPAPI-unprotect the os_crypt.encrypted_key from Local State."""
    try:
        import win32crypt
    except ImportError:
        raise RuntimeError(
            "Windows cookie decryption requires pywin32: pip install pywin32")
    from browser_locator import APP_DIRS
    lap = os.environ.get("LOCALAPPDATA")
    if not lap:
        raise RuntimeError("LOCALAPPDATA not set")
    app_dir = APP_DIRS.get(browser)
    ls = Path(lap) / app_dir / "Local State"
    if not ls.is_file():
        raise RuntimeError(f"Local State not found: {ls}")
    import base64
    data = json.loads(ls.read_text(encoding="utf-8"))
    b64key = data.get("os_crypt", {}).get("encrypted_key")
    if not b64key:
        raise RuntimeError("os_crypt.encrypted_key missing in Local State")
    raw = base64.b64decode(b64key)
    if not raw.startswith(b"DPAPI"):
        raise RuntimeError("Unexpected key format (DPAPI prefix missing)")
    blob = raw[5:]
    try:
        decrypted, _ = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    except Exception as e:
        raise RuntimeError(f"DPAPI unprotect failed: {e}")
    return decrypted


def resolve_key(browser="brave", profile="Default"):
    """Dispatch to the right key resolver for the detected OS."""
    os_name = detect_os()
    if os_name == "macos":
        return resolve_key_macos(browser)
    if os_name == "linux":
        return resolve_key_linux(browser, profile)
    if os_name == "windows":
        return resolve_key_windows(browser, profile)
    raise RuntimeError(f"Unsupported OS for key resolution: {os_name}")


# ---------------------------------------------------------------------------
# Cookie reading
# ---------------------------------------------------------------------------
def read_cookies(browser="brave", profile="Default", domains=None):
    """Decrypt a browser's cookies -> {name: value}, in memory only.

    Args:
        browser: 'brave'|'chrome'|'edge'|'arc'|'opera'
        profile: profile directory name (default 'Default')
        domains: optional list of host substrings to filter (e.g. ['x.com']);
                 None returns all cookies.

    Values are returned in a dict and never printed/persisted here.
    """
    db = resolve_cookie_db(browser, profile)
    if not db:
        raise RuntimeError(f"Cookie DB not found for browser='{browser}' profile='{profile}'")
    key = resolve_key(browser, profile)

    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()
    cookies = {}
    try:
        if domains:
            # Only fetch matching rows (avoid decrypting the whole store).
            clauses = " OR ".join(["host_key LIKE ?"] * len(domains))
            params = [f"%{d}%" for d in domains]
            cur.execute(
                f"SELECT name, encrypted_value FROM cookies WHERE {clauses}",
                params)
        else:
            cur.execute("SELECT name, encrypted_value FROM cookies")
        for name, enc in cur.fetchall():
            if name in cookies:
                continue
            val = decrypt_value(enc, key)
            if val is not None:
                cookies[name] = val
    finally:
        con.close()
    return cookies
