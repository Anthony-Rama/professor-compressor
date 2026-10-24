# Session observability

The private `/ops` dashboard provides a session explorer and reliability,
performance, and adoption summaries. No encoding or media-analysis passes are
added. Tracking is disabled until configured and does not backfill old alerts.

## Configuration

Use the separate development bot or credential-free browser harness locally.
Never run a second process with the production Discord token.

Set these variables in the ignored local `.env`; never commit the actual key:

```dotenv
OBSERVABILITY_DB_PATH=/absolute/private/directory/events.sqlite3
OBSERVABILITY_ADMIN_KEY=replace-with-a-random-secret-of-at-least-32-characters
OBSERVABILITY_RETENTION_DAYS=30
OBSERVABILITY_MAX_EVENTS=100000
DIAGNOSTIC_TEST_GUILD_IDS=comma-separated-development-server-ids
```

Generate the operator key using a password manager or `secrets.token_urlsafe(48)`.
Open `/ops`, username `admin`, password the operator key. Production requires
HTTPS; HTTP is allowed for loopback development. HTTP Basic credentials are not
put in URLs or browser storage by this application. Every data request rechecks
authentication. Browsers can cache HTTP authentication until their session ends;
use a private profile on a shared machine. The dashboard is read-only.

## Docker rollout (after release approval)

1. In the production `.env`, set
   `OBSERVABILITY_DB_PATH=/data/observability/events.sqlite3`, a fresh operator
   key, and the correct development guild IDs.
2. Build the reviewed image. Its non-root user owns `/data/observability`;
   Compose mounts the persistent `diagnostics_data` named volume there.
3. Verify `/ops` denies unauthenticated requests, accepts operator credentials,
   and reports storage ready with no drops or errors.
4. Complete a test-server session. Search its reference, inspect its timeline,
   and verify **Include tests** controls whether it appears in summaries.

Preserve the volume and operator key across releases. Key rotation changes
HMAC user/server pseudonyms and splits cohorts; it does not invalidate upload
sessions or diagnostic references. Do not run `docker compose down --volumes`
during rollout: it deletes history. Disable storage by clearing its DB path if
rolling back. Schema version 1 is checked; an unknown version disables storage
rather than modifying an incompatible database.

A volume on one host is not a disaster-recovery backup. Use SQLite's online
backup API, restrict backup permissions, and apply the same retention policy.
Do not copy only the live `.sqlite3` file while WAL writes are active.

## Identity, privacy, and event contract

A random 128-bit diagnostic ID is independent of the private upload URL. Its
`PC-` reference appears in browser errors and private Discord alerts. Each
manual Compress/retry has a run ID; files use their batch index; automatic
retries record an attempt. Event IDs deduplicate reports.

Authenticated browser reports are limited to 16 KiB, 20 events/report, 1,000
events/session, 120 reports/IP/minute, and a three-second body-read deadline.
Late reporting uses the existing short-lived receipt secret. It cannot change
the authoritative delivery outcome. IPs are used transiently for request limits,
not stored in event history.

Fields are allowlisted and range checked server-side. Never store video bytes,
filenames, captions, raw errors, raw user agents, raw Discord identifiers,
usernames, private links, or credentials in event history. HMAC user/server
pseudonyms enable windowed adoption counts. Browser family/major version,
device category, video properties, error categories, and timings are untrusted
facts for diagnosis, never authorization or delivery decisions. Existing private
Discord alerts retain their separately documented identity data.

Input size is reported before validation where possible. Codec/duration fields
can be absent when metadata fails. Errors use fixed categories; `UNKNOWN` means
the cause has not been established. Raw error text remains in the user's browser.

## Metric definitions and limitations

- The funnel counts distinct sessions, not retries or unique users at each stage.
- Delivered means Discord delivery finished on the relay. Browser success alone
  does not establish delivery. Partial/rejected batches are not successful.
- Retried failures remain visible even when the session later succeeds.
- Cancelled, expired-before-start, replaced, active, and unknown are separate.
  A started session without a final report is unknown, not a confirmed failure.
  Restarting marks unfinished processing/delivery unknown; this feature does not
  make the existing media queue durable.
- Test guilds are excluded by default. Configure the list before recording;
  classification is not retroactive and is not inferred from usernames.
- Timelines use server arrival time, not exact browser execution order. Reports
  are batched and best-effort. Missing data does not prove inactivity.
- Median/p95 stage times use at most 5,000 recent samples per measurement and
  can include failed runs unless filtered. Encoding is per file; other timings
  are per delivery or run. Session creation elapsed is a separate boundary.
- Coarse encoding groups compare container, duration band, and output height.
  Speed is total media seconds / total encoding seconds. It is not a controlled
  device benchmark or a video-quality score.
- Adoption counts cover the selected window, not lifetime retention. Repeat
  usage means at least two delivered sessions for a pseudonymous server. These
  facts do not establish why a server removed the bot.

## Resource limits and failure handling

One background worker owns SQLite writes. A 2,048-entry queue accepts records
without waiting for disk. A full queue drops diagnostics, never videos. Storage
startup/write failures do not fail the bot; `/ops` reports readiness, queued
events, drops, and errors. Browser/network failures can lose reports too.

Retention is at most 30 days with default caps of 100,000 events and 20,000
sessions. Pruning runs at startup and approximately every minute. The database
is capped near 128 MiB; WAL/SHM and rotated container logs take additional space.
Freed DB pages are reused, so the file need not shrink after pruning. Compose
rotates bot logs at 10 MiB × 3 files. Exports need private storage and retention.

Dashboard queries are read-only, limited to two concurrent readers and a SQLite
execution deadline. Results show at most 100 sessions and 500 timeline events.
Check the time window/filters before treating missing rows as missing reports.
External uptime and disk-space monitoring are still needed: a stopped host
cannot report its own outage. No promise of complete event capture is made.

## Verification

Run Python and browser suites, lint/format checks, dependency audits, and a
production-image smoke check before merging. Tests cover privacy filtering,
authentication, rate/body limits, deduplication, retries, late reports,
persistence, retention, and failure isolation. Browser tests use an ephemeral
database and test-only credentials and never connect to Discord. Real usage is
still necessary to evaluate cross-device performance and perceptual quality.

Container CI runs `tests/container_observability.py` twice against the same
disposable named volume (`seed`, then `verify`) to check authentication,
non-root access, filtering, and persistence across container recreation.

The implementation audit also identified the existing `certifi` pin as affected
by [GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2).
This branch updates it to `2026.10.19`; production receives that fix only after
the relevant release is approved, rebuilt, and deployed.
