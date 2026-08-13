# ReapX v2 — Cross-Browser & Cross-Platform Roadmap

Current v1 harvests X bookmarks by driving **Brave on macOS** over CDP, using
the user's logged-in session. v2's goal: work on **any Chromium-family browser**
and **any desktop OS** (macOS, Linux, Windows).

## Why v1 is Brave/macOS-only (the coupling)
The fetcher has hardcoded assumptions:
- `BRAVE_DIR = "~/Library/Application Support/BraveSoftware/Brave-Browser"` (macOS-only path)
- Cookie decryption key from the **macOS Keychain** (`security find-generic-password`)
- Cookie value encryption scheme is Chromium's `v10`/`v11` (AES-128-CBC) — this
  is browser-agnostic *within* Chromium, but the key source differs per OS
- `/tmp` profile dir + `pkill` (Unix-only)
- CDP over `localhost:9222`

## v2 architecture — decouple "browser session" from "harvester"

### 1. Browser detection layer (new `browser_locator.py`)
Detect the user's installed Chromium-family browser(s) and their cookie store:
- macOS: Brave, Chrome, Edge, Arc, Opera — each has a known
  `~/Library/Application Support/<Browser>/<Profile>/Cookies` + Keychain service
- Linux: `~/.config/<browser>/<Profile>/Cookies`; cookie key from a keyring
  (`secret-tool`, `libsecret`, or `keyring` — plaintext fallback via
  `os_crypt.encrypted_key` in Local State for older Chromium)
- Windows: `%LOCALAPPDATA%\<Browser>\User Data\<Profile>\Network\Cookies`;
  cookie key from DPAPI (`win32crypt.CryptUnprotectData`)

### 2. Cookie access abstraction (new `cookie_store.py`)
One interface, platform implementations:
- `decrypt_cookies(browser, profile)` returns `{name: value}` in-memory only
- macOS → Keychain (existing logic, service name per browser)
- Linux → libsecret / keyring
- Windows → DPAPI

### 3. CDP launch abstraction (new `cdp_launcher.py`)
- Locate the browser binary per OS (`which`, `/Applications`, `%ProgramFiles%`)
- Launch headless with `--remote-debugging-port` on a **free** port (not hardcoded 9222)
- Cross-platform process cleanup (no `pkill`; use `proc.terminate()` + `os.kill`)

### 4. Config-driven (new `reapx_config.py` or env/CLI)
- `--browser brave|chrome|edge|arc|auto`
- `--profile Default|Profile 1|auto`
- `--port 0` (auto-free)
- `--output data/x_bookmarks.json`
- Browser path + cookie path resolved by the locator, not hardcoded

### 5. Testing matrix (v2 CI)
- GitHub Actions matrix: `macos-latest`, `ubuntu-latest`, `windows-latest`
- Unit-test cookie decryption with **fixture** (encrypted v10 blob + known key) —
  never real user cookies
- Unit-test browser detection against fake filesystem layouts
- Integration (live session) stays local-only (needs real login)

## Out of scope for v2 (maybe v3)
- Non-Chromium browsers (Firefox/Safari use different cookie encryption + no CDP
  equivalent) — would require profile import or a different extraction strategy
- Android/iOS (X session on device; out of scope for a desktop CLI skill)

## Acceptance criteria (v2 done when)
- [ ] `reapx fetch --browser auto` works on macOS Chrome, Brave, and Edge
- [ ] `reapx fetch` works on Linux (Chrome/Brave) and Windows (Chrome/Edge)
- [ ] No hardcoded browser path, profile, port, or OS-specific command remains
- [ ] Cookie values still never logged/committed
- [ ] CI matrix (3 OS) passes unit + detection tests
