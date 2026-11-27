# Session observability

The private `/ops` dashboard shows current health, an outcome funnel, failures
grouped by cause, performance percentiles, growth, and a per-session timeline.
No encoding or media-analysis passes are added. Tracking is disabled until
configured and does not backfill old alerts. The dashboard uses only the event
history described below; it adds no collection of its own.

## Dashboard sections

**Health now** (top of the page) is a live process snapshot: service readiness,
Discord connection and gateway latency, connected servers from the gateway
cache, release and uptime, active links, uploads, delivery queue depth and
workers, and diagnostic store health (stored events, database size, oldest
event, write errors, drops and queued writes). It ignores the time window and
filters. During a disconnect the server count is labelled last known; before a
connection has populated the cache it shows unavailable rather than zero. No
extra Discord API calls, member intents, guild names or guild IDs are used.

Below it, the **window** (24 hours, 7 days, 30 days or 90 days), the **Include
test servers** setting and an optional category filter apply to every tab:

- **Overview**: session stat cards, the outcome funnel (link created → opened →
  files selected → compression started → uploaded to relay → delivered), final
  outcomes, a delivered-versus-failed trend (hourly for 24 hours, otherwise per
  UTC day, with a data table), and the top three failure groups.
- **Failures**: failures grouped by error code and stage with sessions, share,
  sessions later delivered, last seen, browser and device mix and the three most
  recent references; engine signals; latest session states.
- **Performance**: p50/p90 for encode speed, compress-to-delivery time, relay
  receive, queue wait, Discord delivery and other stage timings; file sizes;
  engine and processing-path shares; comparable encoding groups.
- **Growth**: installs, removals (with likely reconnect artifacts separated),
  net change, servers with delivery, a connected-server chart and a daily table
  of installs, removals, sessions and deliveries; input formats, browsers and
  devices.
- **Sessions**: a reference lookup with the session's timeline, file
  diagnostics and attempt evidence, a per-session JSON export, and the 100 most
  recently updated sessions.
- **System**: storage details, process counters and release comparison.

**Worth a look** lists anomalies for the current view (see `anomalies` below).
**Copy summary** copies a short Markdown report of the view: health, funnel,
top failure groups with example references, percentiles, growth and anomalies.
It contains no pseudonymous user/server keys, internal session IDs, filenames
or other identifiers beyond diagnostic references and release names, so it can
be pasted into an issue or a chat when investigating. If the browser blocks the
clipboard, the text is shown for manual copying. **Export JSON** saves the
latest successfully loaded view; stale or failed refreshes cannot export old
data.

Daily figures use UTC; event times are shown in the operator browser's local
timezone. Growth and daily activity ignore the category filter. File-size
summaries count the latest server receipt per file index in delivered sessions
with both measurements; input sizes remain browser-reported. Private filenames
are not collected.

Blocked before compression counts distinct sessions with a browser-reported
preparation failure and no recorded compression start. Filter by error code
`INPUT_WRITE_UNSUPPORTED` for browsers that lack the temporary-file write
capability. Older generic storage errors are not reclassified.

Process counters and storage-error/drop counts reset on restart and include tests.
Optional auto-refresh runs every 30 seconds only while the dashboard is visible
and no refresh is already pending. Every request is authenticated.

### Likely reconnect removals

Discord can report a few servers as removed while a restarting bot refills its
cache. Without changing stored history, a removal is marked as a likely
reconnect artifact when it is recorded within 60 seconds after the current
process started, within 180 seconds after a recorded `interrupted` shutdown
marker, or as part of two or more removals within 60 seconds of the first event
recorded by a release. A restart of the same release with no active sessions
leaves no marker, so an older artifact can still count as a removal. Removal
counts that exclude artifacts are labelled as such.

## `/ops/data` reference (schema version 1)

`GET /ops/data` returns JSON with `Cache-Control: no-store`, behind the same
Basic authentication, rate limits and two-reader limit as `/ops`.

Query parameters:

| Parameter | Meaning |
| --- | --- |
| `days` | Window length in days, clamped to 1–90 (default 7). Not an integer: 400. |
| `tests` | `1` includes test-server sessions. |
| `dimension`, `value` | Optional category filter: `stage`, `error_code`, `engine_signal`, `input_format`, `browser`, `device`, `engine`, `path` or `revision`. |
| `reference` | Optional `PC-` plus 12 hex digits. Surrounding spaces and lower case are normalized; any other value returns 400. Filters the session panels and adds `lookup`. |

Times are Unix seconds (UTC); durations are seconds; sizes are bytes; shares
and rates are percentages rounded to one decimal place. `null` means not
measurable (for example a rate with no denominator). Browser-reported values
are untrusted. Additive fields may appear without a version change; a removed
or redefined field increments `schema_version`.

| Field | Contents |
| --- | --- |
| `schema_version` | `1`. |
| `generated_at` | Response time. |
| `window` | `days`, `start`, `end`, `previous_start` (the comparison window starts here and ends at `start`), `include_tests`, plus `dimension`/`value`/`reference` when filtered. |
| `runtime` | Live process snapshot: `ready`, `discord_ready`, `draining`, `commands_synced`, `gateway_latency_ms`, `connected_servers`, `server_count_current`, `unavailable_servers`, `revision`, `active_sessions`, `active_uploads`, `queue_depth`, `queue_capacity`, `delivery_workers`, `worker_capacity`, `buffer_bytes`, `buffer_limit_bytes`, `counters` (process counters including `uptime_seconds`). |
| `storage` | Writer health: `configured`, `ready`, `queued`, `dropped`, `errors` (since process start), `retention_days`, `max_events`. When storage is not ready, only `storage`, `sessions` and `events` come from the store. |
| `store` | Retained history: `events`, `sessions`, `oldest_event`, `newest_event`, `database_bytes`, `free_bytes`, `wal_bytes`. |
| `funnel` | `steps`: `{step, sessions, step_percent, overall_percent}` for `created`, `opened`, `selected`, `started`, `uploaded`, `delivered` (a later step counts as reaching the earlier ones). `outcomes`: exclusive final outcome counts `delivered`, `failed`, `cancelled`, `in_progress` (updated within 2 hours), `unknown` (started, no confirmed result), `abandoned` (opened, never started), `expired` (never opened). `failure_percent` = failed / (delivered + failed). `bucket_seconds` (3600 for a 1-day window, else 86400) and `trend`: `{start, sessions, delivered, failed}` per bucket of session creation time, empty buckets omitted. |
| `comparison` | The previous, equally long window: `sessions`, `outcomes`, `failure_percent`, `error_codes` (sessions per reported error code). |
| `failures` | `sessions` (distinct sessions with a reported failure), `group_count`, `truncated` (only the latest 20,000 session/cause rows were read), `groups` (top 12): `error_code`, `stage`, `sessions`, `reports`, `recovered` (later delivered), `share_percent` (of failing sessions), `last_seen`, `browsers`, `devices` (sessions per value), `references` (up to 3 latest). Failures are `client_failed`, `upload_failed`, `delivery_failed` and failed `stage_finished` steps with an error code; `CANCELLED` is excluded. |
| `percentiles` | `{samples, p50, p90}` over up to 5,000 recent samples each: `encode_speed` (media seconds per encoding second, re-encoded files only), `time_to_delivery_seconds` (server arrival of Compress to server-confirmed delivery), `relay_receive_seconds`, `queue_seconds`, `input_bytes`, `output_bytes` (per file, from relay receipts). |
| `timings` | `{samples, p50, p90, p95}` per stage timing: `engine_seconds`, `encoding_seconds`, `relay_receive_seconds`, `queue_seconds`, `discord_delivery_seconds`, `total_seconds`. |
| `growth` | `installs`, `removals`, `likely_reconnect_removals`, `net_change` (installs minus removals that are not artifacts), `truncated`, `daily` (`day`, `installs`, `removals`, `likely_reconnect_removals`, `connected_servers` at the last change that day), `events` (latest 200: `at`, `event`, `connected_servers`, `likely_reconnect`). |
| `anomalies` | `{severity, code, message}`, warnings first. Codes: `service_not_ready`, `discord_disconnected`, `draining`, `queue_near_capacity`, `storage_unavailable`, `storage_loss`, `failure_rate_spike` (at least 10 points and 1.5× the previous window, each with 5+ finished sessions), `failure_rate_high` (25%+ with no comparable previous window), `new_error_code` (2+ sessions, absent from the previous window), `unknown_outcomes`, `deliveries_dropped`, `net_server_loss`, `failures_truncated`. |
| `summary` | The Markdown text used by **Copy summary**. |
| `lookup` | Only with `reference`: `reference`, `found`, `session` (`reference`, `created`, `updated`, `revision`, `is_test`, `status`, `files`, `failures`, `failure_data`) and `events` (up to 500, oldest first: `at`, `run_id`, `file_index`, `event`, `source`, `data`). Pseudonymous keys and the internal session ID are omitted. An unknown reference returns `found: false`. |
| Earlier panels | `totals`, `statuses`, `groups` (top 20 per category plus `revision` and `failure_stage`), `activity` (UTC day × event counts), `adoption`, `performance` (comparable encoding groups), `files` (adds `unchanged_percent`, `remuxed_percent`), `retries`, `collection`, `sessions` (latest 100, including pseudonymous `user_key`/`guild_key`), `events` (the reference's timeline). |

The page's **Export session JSON** saves `lookup` with `schema_version` and an
`exported_at` time.

## Configuration

Use the separate development bot or credential-free browser harness locally.
Never run a second process with the production Discord token.

Set these variables in the ignored local `.env`; never commit the actual key:

```dotenv
OBSERVABILITY_DB_PATH=/absolute/private/directory/events.sqlite3
OBSERVABILITY_ADMIN_KEY=replace-with-a-random-secret-of-at-least-32-characters
OBSERVABILITY_RETENTION_DAYS=90
OBSERVABILITY_MAX_EVENTS=300000
DIAGNOSTIC_TEST_GUILD_IDS=comma-separated-development-server-ids
```

For an existing deployment, explicitly update its private environment file to
the 90-day/300,000-event values above; an existing 30-day setting still takes
precedence over the new defaults. Select **90 days** in the dashboard to
search the full window. Older history that has already been pruned cannot be
recovered by extending retention.

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
   and verify **Include test servers** controls whether it appears in summaries.

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

### Engine failure evidence

The private `/ops` dashboard uses this same bounded SQLite event history; there
is no second raw-log database. A session lookup includes files that failed
before upload, grouped by manual run and file number, plus an attempts/evidence
table. The category filter accepts `engine_signal`; Export JSON includes
the sanitized fields in the selected view.
File summaries use client sequence within each run when available, so late
batches do not overwrite newer browser facts. Relay receipt values take
precedence; the timeline itself continues to show server arrival order.

Engine messages are inspected locally (at most 2,048 characters per message)
and discarded by the diagnostic reporter. Only fixed signatures such as `oom`,
`allocation_failed`, `wasm_bounds`, or `unsupported_codec` are sent. The
classification preserves its source (`engine_log` or `exception`) and a fixed
exception-kind category. Explicit OOM/allocation evidence takes precedence over
a misleading wrapper error. A WASM bounds trap is **not** proof of OOM and uses
`WASM_TRAP`. Signals reset between operations, files and retries. Exit codes
apply to exec operations, not ffprobe's nonstandard success return value.

Attempt records include automatic attempt number, requested decoder mode/thread
count, encoder threads/preset/mode/bitrate, encode-pass and size-adjustment
indicators, engine and asset build. HTTP failures can include a numeric response
status. `auto` means
the decoder chooses internally; its actual thread count is unknown. Client
sequence and monotonic elapsed time help interpret batched arrival order; they
are untrusted, not authoritative execution timestamps. Processing checkpoints
are emitted no more often than every 15 seconds while progress arrives, never
on every frame. Last media time and time since the latest progress are recorded
on completion/failure. A crash can still prevent all final reports.

The existing metadata probe also requests profile, level, pixel format, color
transfer, nominal/average frame rates, rotation and audio properties. No extra
probe/encode or frame scan is added. Missing measurements are omitted rather
than represented as zero. Differing nominal/average rates are a hint, not a
reliable variable-frame-rate detector. Optional tags, titles, creation times,
locations and full metadata objects are not logged. Hardware/RAM/GPU probes,
persistent device identifiers and automatic raw-log/video uploads are not added.

No new consent-dependent advanced-device report is collected. Introducing one
requires a separate, user-facing opt-in and privacy review. Existing retention,
private access, range checks and event/report/queue caps apply to all new fields.
Transport batches are additionally bounded to approximately 12 KiB of events
to stay below the 16 KiB request cap. Diagnostic delivery remains best-effort.
The compact terminal-failure report includes sanitized input metadata when
available and retries once after a network error or transient 408/5xx response.
Retries retain the same run ID for deduplication. Neither transport can guarantee
a report after a browser crash, tab closure or permanent network loss.

Recent sessions include a plain-language explanation for the active run's
reported failure. The timeline distinguishes a failed step from a step ending,
and source labels such as browser/server are separate from the cause. `UNKNOWN`
means the client did not capture the cause; it is never interpreted as memory
exhaustion. Existing old records cannot be enriched retrospectively.

The application-owned `encoder-client.js` transport uses the unmodified pinned
FFmpeg worker protocol. Worker `error`, `messageerror`, malformed RPC replies,
and synchronous post failures reject pending operations instead of leaving the
UI waiting indefinitely. Only `WORKER_CRASH` / `WORKER_MESSAGE_ERROR` categories
are reported, never the browser's raw error, script URL or stack. A worker crash
does not by itself prove memory exhaustion. Cancellation remains distinct and
terminates pending operations without being counted as a processing failure.
Keep transport regression tests in the dependency-upgrade review: the adapter
implements only the worker RPC methods this application uses.

During encoding/remuxing, 120 seconds without advancing progress shows a
non-fatal warning and emits at most one `processing_stalled` event per operation.
Progress resumes normally without an automatic timeout/re-encode. The warning
clears on advancing progress or operation completion/cancellation. Background
timer throttling can delay detection; no warning proves that the engine crashed.
Advancement uses per-operation high-water marks; repeated, regressing or invalid
progress cannot indefinitely suppress the warning. Recorded media time and
percentage reflect the furthest reported progress within that operation.
No additional encoding passes or video copies are introduced.

Sessions without a confirmed final outcome remain explicitly unknown in the
dashboard. Missing telemetry is not classified as a confirmed compression
failure. Existing storage errors/drops and unavailable-storage warnings should
be monitored alongside processing failures.

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
- Median/p90/p95 stage times use at most 5,000 recent samples per measurement and
  can include failed runs unless filtered. Encoding samples require a file-ready
  report and include that file's failed encoding attempts before recovery;
  files that never became ready are not included in those encoding percentiles.
  Their failed-stage times remain in the timeline. Other timings are per
  delivery or run. Session creation elapsed is a separate boundary.
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

Retention is up to 90 days with default caps of 300,000 events and 60,000
sessions. Pruning runs at startup and approximately every minute. The database
is capped near 384 MiB; WAL/SHM and rotated container logs take additional space.
The row/disk safeguards can shorten the available history during unusually high
volume; 90 days is an upper retention bound, not a promise of unlimited capacity.
Freed DB pages are reused, so the file need not shrink after pruning. Compose
rotates bot logs at 10 MiB × 3 files. Exports need private storage and retention.

Dashboard queries are read-only, limited to two concurrent readers and a SQLite
execution deadline shared by every panel of one request. Results show at most
100 sessions, 500 timeline events, 12 failure groups with 3 references each,
5,000 samples per percentile and 2,000 install/removal events. All panels read
one consistent snapshot; the window's events are scanned a bounded number of
times, and the previous-window comparison reads only session rows and failure
reports. On a 300,000-event store a 90-day view took about 2.6 seconds on a
development laptop; a slower host can reach the 3-second deadline, which
returns 503 for that request without affecting the bot.
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

Container CI runs `tests/harness/container_observability.py` twice against the same
disposable named volume (`seed`, then `verify`) to check authentication,
non-root access, filtering, and persistence across container recreation.
