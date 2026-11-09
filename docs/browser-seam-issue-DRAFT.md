# The browser-seam issue — filed upstream as #150960

<https://github.com/NousResearch/hermes-agent/issues/150960>

Kept in-tree as the record of what was asked for and why (catalog rule 11's
"ask for a hook"). The text below is the issue body as filed; the two
`harvest_mode` values in it name the two shapes the plugin offered **at filing
time** (browser inside the plugin). The shipped plugin has since dropped
browser mode — it is data-in only, and the browser path is the standalone
scripts — so read those two lines as the historical posture, not as current
settings.

> **Title:** Plugin surface for consented, read-only access to the user's
> own logged-in browser profile (a browser-session seam for
> bookmark-harvester plugins)
>
> **Is your feature request related to a problem?**
>
> Yes — a plugin-surface gap that catalog rule 11 leaves us (correctly)
> nowhere to put a whole class of integrations.
>
> **The plugin.** [ReapX](https://github.com/BigBossRabbit/reapx) harvests
> the user's own X bookmarks — without the paid X API — by driving the
> user's own logged-in browser session: it copies the browser's `Cookies`
> SQLite **read-only** into a fresh temporary profile, launches the
> browser **headless** over CDP on a free port, and lets the browser
> authenticate to x.com exactly as it would in normal use. Cookie values
> are never decrypted, read, logged, or persisted; a dead session is
> detected as a login redirect; the temp profile is always deleted; the
> source profile is never modified.
>
> **The gap.** R11 says a plugin never reads "another tool's login (a
> vendor CLI's token file, **a browser profile, a keychain**) or any other
> credential store directly", and that "if Hermes has no route for a
> credential a plugin needs, ask for one". X has no Hermes OAuth route
> (and no usable free API), and the user's browser profile is precisely
> the "login" this class of plugin needs — as a browser, not as a token.
> We are not asking to be allowed to work around it; we are asking for
> the proper seam. Today there is no capability id and no host helper for
> "the user's own logged-in browser", so a compliant plugin literally
> cannot offer bookmark harvesting, even fully consented.
>
> **What we ship meanwhile (both modes, no workaround asked to be
> blessed):**
> 1. `harvest_mode: export` — a strictly-R11-clean mode that processes a
>    user-supplied bookmarks file (or an array the *agent* collected
>    through its own, already-consented browser tools) with zero browser
>    reads.
> 2. `harvest_mode: browser` (default) — the read-only cookie-DB-copy
>    path described above, declared in our settings, disclosed in the
>    README and the catalog PR (with a link to this issue), and designed
>    so that the moment a real seam lands we gate on it.
>
> **What we're asking for** (any one of; in order of our preference):
> 1. **A consented, auditable host helper** in the spirit of
>    `agent.secret_scope.get_secret` for env secrets and
>    `credentialed_provider_request` for sign-ins — e.g.
>    `agent.browser_session.copy_cookie_store(browser, profile, *,
>    domains=…)` (or `launch_with_cookie_store(...)`) that performs the
>    read-only copy itself, enforces copy-only/never-decrypt semantics,
>    writes one audit line, and **raises/fails closed when the host
>    policy says no** (cron, unattended, not-logged-in) — R12-clean.
> 2. **A declared capability id** (e.g. `browser.read_profile`) with the
>    standard consent screen ("Read bookmarks/pages from your own
>    logged-in browser via a read-only copy of its session; nothing is
>    decrypted"), so we can
>    `if ctx.has_capability("browser.read_profile"): harvest() else:
>    export_mode() + notice` — probe-then-degrade, exactly the
>    capabilities contract.
> 3. **An agent-browser variant**: a sanctioned way for a plugin to
>    request a browser session inside the existing Hermes browser surface
>    (the `SUPERVISOR_REGISTRY.capture` trusted-CDP route is the
>    precedent for plugin CDP access) for the case where the user is
>    logged into the relevant site in the Hermes browser profile.
>
> **Why a seam and not a "no"**: we think "the user's own logged-in
> browser, used as a browser, read-only, consented, audited" is the same
> shape of problem `get_secret` and `credentialed_provider_request`
> already solved for other credential classes — a host-owned, consented,
> fail-closed route. We would much rather build on one than ship a file
> copy that a strict R11 reading calls a workaround. We are a concrete
> consumer for the hook today (bookmark harvesting; the same seam would
> serve any "my logged-in browser is my data source" integration), and
> we're happy to co-design + PR tests.
>
> **Security properties we already guarantee and would welcome in a
> helper**: source profile never written; copy is the only read; no
> decryption (Keychain/libsecret/DPAPI entirely out of the plugin); no
> cookie value ever crosses a log or a network it didn't already belong
> to; temp profile deleted in `finally`; free (never hardcoded) CDP port;
> browser process never survives the run (terminate→kill, no pkill);
> dead-session = login-redirect = clean error, no prompt loops under
> cron; all X pages read are the user's own (history/follow lists), no
> third-page crawling.
