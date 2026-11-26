# Development

This is the maintainer's development and test workflow. Bug reports and feature
suggestions are welcome, but outside code contributions are not currently
accepted. See the README for the original code's reuse terms.

## Maintainer development workflow

1. Create a focused branch from `main`.
2. Create a private `dev.env` for the separate development Discord application.
3. Install Python and browser dependencies.
4. Add or update tests for behavior changes.
5. Run the complete local quality suite before opening a pull request.

```bash
python -m pip install -r requirements-dev.txt
npm ci
python -m ruff check .
python -m ruff format --check .
python -m pip_audit --requirement requirements.txt
PYTHON_DOTENV_DISABLED=1 python -m unittest discover -s tests -v
python -m compileall -q bot.py professor_compressor tests
npm audit --omit=dev
npx playwright install chromium
npm run test:browser
docker build --tag professor-compressor:local .
```

Browser tests also require `ffmpeg` on PATH and simulate Discord delivery.

Use the protected local `qa` branch with the isolated dev bot for manual tests.
Start from the current `main` and test only the candidate changes you intend to
release. Merge the reviewed feature branch, not the entire QA branch. Before
deploying session, relay, or delivery changes, run a live `/compress` on the dev
bot and check delivery status after an interruption. For browser changes, include
a Safari smoke test and a Windows SteelSeries batch when those devices are available.

Keep pull requests small enough to review. Explain the user-visible behavior,
the security or privacy impact, and how the change was verified.

## Repository rules

- Never commit `.env`, credentials, webhook URLs, private keys, production IP
  addresses, user content, or private upload links.
- Diagnostic history must follow [the observability schema](docs/observability.md):
  allowlisted numeric media measurements and keyed user/server pseudonyms only.
  Never add media contents, filenames, captions, raw identifiers, IPs, credentials,
  private links, or raw errors. Existing private operator alerts have a separate
  identity-data policy documented in the Privacy Policy.
- Keep browser compression local. Only completed output files may cross the
  relay boundary.
- Pin production dependencies and explain new third-party services.
- Treat filenames, HTTP headers, Discord names, and uploaded bytes as untrusted.

Security reports must be sent privately, not in public issues. See
[SECURITY.md](SECURITY.md) for the email address and reporting instructions.

## Private development configuration

### Isolated dev-bot launcher

Use a separate Discord application named Professor Compressor Dev. Create
`dev.env` from `dev.env.example` and set owner-only permissions (`chmod 600
dev.env` on macOS/Linux). Never copy production's token or dashboard key. The
dev file is excluded from both Git and Docker builds. `DEV_APPLICATION_ID` is
the dev application's ID, not the test server ID; `ALLOWED_GUILD_IDS` contains
the test server ID(s).

```bash
.venv/bin/python dev_bot.py --check
.venv/bin/python dev_bot.py
```

The check performs a read-only Discord identity request and refuses mismatched
or production identities before starting a gateway connection or HTTP server.
The launcher never loads `.env` and does not inherit application settings,
Python import overrides, or proxy settings from the parent shell. It binds only
to loopback, restricts commands to the configured servers, disables Top.gg/DSC
publishing and alert webhooks, and records diagnostics separately at
`observability-data/dev/events.sqlite3`. An optional, separate
`OBSERVABILITY_ADMIN_KEY` enables the dev `/ops` dashboard. Without that key,
the dashboard is disabled; do not reuse the production key.

For a Windows browser testing a Mac-hosted dev bot, expose the local server
through a temporary HTTPS tunnel, for example:

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

Set `PUBLIC_BASE_URL` to the generated HTTPS origin before starting the dev bot.
Keep the Mac awake and both processes running. A new Quick Tunnel gets a new
URL: update the dev file and restart the bot before creating new sessions.
Treat the tunnel as public, not authenticated by the obscurity of its address;
keep session URLs private and any dev dashboard password-protected.

Cloudflare upload limits can reject large batches before they reach the relay.
Start with single clips or small batches. For full-size batch acceptance testing,
verify the tunnel's request-body limit or use a connection without that limit;
do not mistake a tunnel rejection for a compressor regression. See
[Cloudflare's 413 documentation](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-413/).
The finished batch is sent in one HTTP request, not one request per video.

Stop the dev process before switching branches. Restart it after Python changes
and create a fresh `/compress` session after restarting. Keep production on
`main`; test feature branches with the dev identity. No production deployment
is required for this workflow.

### Browser-local input preservation acceptance

Selected sources are copied into OPFS in 1 MiB reads, with a 1 GiB per-file and
4 GiB per-page batch cap (10 files maximum). This adds local disk I/O, not server
storage. The copy must finish before the file is marked Ready or compression
can start. Chrome/Edge with OPFS and Web Locks support are required for this
workflow; missing/blocked storage fails explicitly rather than retaining an
unsafe source reference or falling back to unbounded RAM copies.

Selection admission checks browser storage estimates with headroom of at least
256 MiB or 10% of remaining quota, whichever is larger. Pending local-copy
reservations are counted conservatively because in-flight writes may not yet
appear in usage; completed copies are not counted twice. Estimates are not free-disk guarantees: unavailable, invalid,
or timed-out estimates fall back to the hard caps and handled write failures.
Over-limit additions are rejected before copying without disturbing prior clips.

Preparation records batched, allowlisted stage events with a separate run ID, sizes,
durations and error codes, never filenames, paths, captions, or file content.
They appear under the preparation stage/error filters and session timeline but
do not mark compression as started or failed. Reporting is best-effort and
cannot block selection or copying. Module loading and storage initialization
have bounded waits; late storage handles/writers are cleaned up after timeout.

Anonymous per-page directories and browser locks prevent one tab's cleanup
from deleting another tab's active input. Removing a selection aborts its copy;
successful delivery and idle session expiry close the local store. Input copies
are retained across compression cancellation and delivery retries because a
prepared unchanged MP4 may still depend on that local file. Page-exit cleanup
is best-effort; a later visit removes unlocked abandoned directories in the
application's own namespace. A refresh always requires reselecting clips.
Abandoned-copy cleanup requires revisiting the same origin. When a temporary
tunnel URL changes, clear the old origin's browser site data if its page-exit
cleanup was interrupted; the new origin cannot access the old origin's storage.

On Windows, export/drop a GG clip, wait for Ready, export/drop another, then
compress. Compare with saved-folder selections and test removing/reselecting
one failed item without losing the caption or other prepared clips. Automated
tests replace a real disk source after Ready and verify the uploaded bytes,
exercise source read errors, quota denial, bounded reads and preparation
timeouts, cancellation, storage/module unavailability, and cross-tab cleanup.
These tests do not establish SteelSeries GG behavior or performance on Windows;
complete that manual acceptance before release.

### Direct application entry point (manual configuration)

Create `.env` locally; it is ignored by Git and Docker's build context. Set
`DISCORD_TOKEN` to the separate development bot's token and `PUBLIC_BASE_URL`
to the address that your test browser can reach (for a local desktop test,
`http://127.0.0.1:8080`). Run `python bot.py`. Do not run a second instance using
the production bot token. Install the development bot with View Channel, Send
Messages, and Attach Files; thread tests also need Send Messages in Threads.

The browser tests do not need credentials and never connect to Discord.
On a Mac with Chrome already installed, set `CHROME_EXECUTABLE` to the Chrome
executable and `TEST_PYTHON` to the Python environment used for the test server.

The normal bot-message size target excludes the invoking user's Nitro allowance.
`BOT_BASE_UPLOAD_MIB` defaults to 20, following the current
[Discord upload reference](https://github.com/discord/discord-api-docs/blob/main/developers/reference.mdx#uploading-files).
Guild boost allowances can raise this limit. The interaction allowance is used
only as an additional upper bound. The pinned discord.py release still carries
an older base allowance, so review this override whenever Discord changes its
limits; do not increase it just to accept larger files.

## Readiness and releases

### Optional Top.gg server-count integration

Set `TOPGG_API_TOKEN` in the private deployment `.env` to a **v1 project token**
from the bot's Top.gg dashboard → Integrations & API. Do not use a legacy v0
token, an OAuth token for another project, or your Discord bot token. Never
paste the token into chat, commit it, or put it in browser JavaScript.

The worker uses the [v1 metrics endpoint](https://docs.top.gg/api/v1/projects)
and sends only `server_count`, not server IDs, member counts, or user data.
It posts after Discord becomes ready and every hour by default. Guild joins
and removals trigger a coalesced update with at least 60 seconds between
successful attempts. `TOPGG_STATS_INTERVAL_MINUTES` can change the periodic
interval (minimum 30 minutes). Disconnections pause updates until ready again.
HTTP errors/timeouts retry no sooner than an hour; 429 responses also respect
`Retry-After`. This background task cannot block compression or delivery.

An absent token disables the integration without affecting the bot. After
deployment, verify a `Published Top.gg server count` log and the listing's
count. A mock API test cannot establish that a real token is accepted.

- `/healthz` is HTTP liveness and aggregate load; it also includes `revision`.
- `/readyz` returns 200 only when Discord is connected, commands are synced,
  every configured delivery worker is alive, and the process is not draining.
  Otherwise it returns 503. Docker checks this endpoint. Configure external
  monitoring against `/readyz`; an unhealthy Docker container is not automatically
  restarted by `restart: unless-stopped`.
- Set `APP_REVISION` to the release commit when building the image, for example
  `APP_REVISION="$(git rev-parse HEAD)" docker compose build bot`.
- SIGTERM/SIGINT stop new sessions and allow active work up to 120 seconds to
  finish. Compose allows 150 seconds before force-stopping the process. Do not
  use `docker kill` or shorten that grace period for a routine release.
- Sessions and delivery receipts are in memory. A restart still invalidates
  idle links and can interrupt work longer than the grace period. Deploy at a
  quiet time and check both readiness and the release revision afterward.
- Browser package URLs include a version fingerprint and are immutable. Legacy
  unversioned paths remain available without long-lived cache headers. Change
  package versions when updating assets; do not modify installed npm distributions.

## Live acceptance checks

Use a dedicated development server, not a user's channel. Check non-Nitro and
Nitro accounts in an unboosted server, a boosted server, a permission-denied
channel, normal and private threads, and a permission change after creating a
link. Verify the actual attachment and audio playback, not only the success
alert. Also check Firefox/Safari, portrait and variable-frame-rate recordings,
and large media on a memory-constrained device. Automated tests simulate Discord
delivery and do not establish these external-platform results.
