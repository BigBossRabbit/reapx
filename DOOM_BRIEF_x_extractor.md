# MISSION BRIEF — Doctor Doom — Fix the X follower/following extractor

GOAL
Repair `/Users/fromthejump/reapx/scripts/fetch_x_graph.py` so it extracts CLEAN,
CORRECT per-user data for the maximum number of @OKIN_17's followers and following
that X will serve, then run it and produce a verified dataset on disk.

## CONTEXT — pinned ground truth, do NOT re-derive or assume

The script already works mechanically (CDP + Brave cookie-copy, modelled on
`scripts/fetch_x_bookmarks.py`). Scrolling, dedupe and output are fine. The DATA
PARSING is wrong. I dumped the live DOM with `scripts/dump_x_dom.py` and these are
FACTS observed on the real page, not theories:

1. HYDRATION TIMING (this is the biggest bug)
   - At 10s after `Page.navigate` to `https://x.com/OKIN_17/followers`:
     `document.querySelectorAll('[data-testid="UserCell"]').length` === **0**
     and `document.body.innerHTML` still shows the `JavaScript is not available`
     noscript shell + Grok/chat drawers only.
   - At ~20s: **79** UserCells present.
   - Current code sleeps 8s then starts scrolling → it scrolls a near-empty DOM,
     which is why one run returned only 3 followers / 53 following.
   - FIX: poll for `UserCell` count >= 5 with a hard ceiling of ~45s BEFORE the
     first scroll, and keep polling after each navigate. Do not use a blind sleep.

2. THE NAME SPAN IS `dir=null`, NOT `dir="auto"`
   - Verbatim from the dump, the display-name spans are:
     `class="css-1jxf684 r-dnmrzs r-1udh08x r-1udbk01 r-3s2u2q r-bcqeeo r-1ttztb7 r-qvutc0 r-poiln3"` with `"dir": null`.
   - The current selector `a[role="link"] span[dir="auto"]` therefore matches
     nothing and the code falls through to aria-label/button text. That is why
     every record came back as `bio: "Click to Follow <handle>"` and `name: ""`.

3. HANDLE IS DETERMINISTIC — use this, not href regex
   - Every cell contains `data-testid="UserAvatar-Container-<handle>"`.
     Example from the dump: `data-testid="UserAvatar-Container-Kamuoshi"`.
   - Parse the handle from that attribute. Keep the href match only as a fallback.

4. PARSE `innerText` LINES STRUCTURALLY — this is the reliable path
   Real cell innerText, verbatim:
   ```
   K
   @Kamuoshi
   Follows you
   Follow back
   is back.
   ```
   Structure: line[0] = display name, line[1] = `@handle`,
   then OPTIONAL `Follows you`, then the button label
   (`Follow` | `Follow back` | `Following` | `Unfollow`), then the bio (may be
   multi-line, may be absent).
   - Anchor on the `@handle` line's index. Name = the line immediately BEFORE it.
   - Bio = all lines AFTER the handle line, minus these known non-bio tokens:
     `Follows you`, `Follow`, `Follow back`, `Following`, `Unfollow`,
     `Pending`, `Click to Follow …`, `Click to Unfollow …`, and bare
     count/date fragments.
   - A name may legitimately be one character (e.g. `K`) — do NOT discard short names.

5. CAPTURE `Follows you` AS A FIRST-CLASS FIELD — new requirement
   X tells us the mutual relationship directly. Add a boolean field
   `follows_you` per user. This is more trustworthy than intersecting two
   scraped lists, and it is what the graph layer needs.

6. VERIFIED BADGE + AVATAR already work. Keep that logic. Verified is currently
   detected via `[data-testid="icon-verified"]` / `svg[aria-label*="Verified"]`.

7. FOLLOWER/FOLLOWING COUNTS AND LOCATION ARE NOT IN THE LIST VIEW.
   Do NOT fake them. Emit them as `null` and note the limitation in the output
   JSON's `meta` block. Do NOT navigate to 1,600 individual profiles — that will
   get the account rate-limited or flagged. If you want per-user counts, expose it
   as an OPT-IN `--enrich N` flag that visits only the top N profiles with a
   >=3s delay between them, defaulting to OFF.

8. KNOWN CEILING — set expectations honestly in the output.
   The account has **4,706 followers**. X's `/followers` timeline stops serving
   new cells at roughly **1,400-1,600** for this session. Best runs so far:
   1,600 followers / 1,547 following. If you cannot exceed that, that is an X-side
   pagination limit, NOT a bug — record the achieved ceiling in `meta` and say so
   plainly in your self-report. Do NOT pad the dataset to make the number look better.

## DELIVERABLE

1. Fixed `/Users/fromthejump/reapx/scripts/fetch_x_graph.py`.
2. A real run of it writing `/Users/fromthejump/reapx/data/x_graph_clean.json`,
   with a `meta` block containing: `extracted_at`, `followers_extracted`,
   `following_extracted`, `profile_followers_total` (4706 if detectable),
   `ceiling_note`, and `fields_unavailable: ["followers_count","following_count","location"]`.
3. A short self-report printed at the end with the ACTUAL counts and the
   percentage of records that have a non-empty `name` and a non-empty `bio`.

## CONSTRAINTS

- Do NOT use the X API and do NOT touch `xurl` — OAuth is not set up and the user
  must not be asked for credentials. CDP + Brave cookie-copy only.
- Do NOT log, print, or write cookie values, auth tokens, or the cookie DB path
  contents anywhere. Copy the DB as the existing code already does; nothing more.
- Do NOT open a visible browser window that steals the user's focus if avoidable;
  keep the existing headless/temp-profile launch pattern from `cdp_launcher.py`.
- Do NOT modify `browser_locator.py`, `cdp_launcher.py`, or `reapx_config.py`.
- Do NOT delete or overwrite the existing `data/x_graph*.json` files — write to the
  NEW path `data/x_graph_clean.json`.
- macOS has no GNU `timeout` — do not use it.
- Do NOT fabricate. If a field is unavailable, emit null and say so.

## SUCCESS WHEN

All of the following are true and you have PROVEN each by running a command:
1. `data/x_graph_clean.json` exists and parses as JSON.
2. `>= 95%` of follower records have a non-empty `name` that is NOT one of the
   button strings (`Follow`, `Follow back`, `Following`, `Unfollow`) and does NOT
   start with `Click to `.
3. `0` records contain `Click to Follow` or `Click to Unfollow` in ANY field.
4. `follows_you` is present on every record and is `true` for at least some of them.
5. Follower count extracted is `>= 1600` (the current best) — or, if lower, you
   have explicitly diagnosed and reported why.
6. You print the verification commands and their real output in your self-report.

## PRIORITY

High — this dataset is the foundation of a graph CRM build. Every downstream
step (Neo4j import, community detection, influence scoring) is blocked until the
data is clean. Garbage in = the whole CRM is worthless.
