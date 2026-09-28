# Architecture

Professor Compressor keeps CPU-heavy video encoding in the user's browser.
The hosted service coordinates short-lived sessions and relays only completed
MP4 files to Discord.

```mermaid
flowchart LR
    U[Discord user] -->|/compress| D[Discord API]
    D --> B[Python bot]
    B -->|Single-use HTTPS link| U
    U -->|Open private session| W[Browser UI]
    W -->|Load static assets| R[aiohttp relay]
    W -->|Compress locally| F[FFmpeg WebAssembly]
    F -->|Validated MP4 batch| R
    R -->|Bounded in-memory queue| Q[Delivery workers]
    Q -->|One message with attachments| D
    C[Caddy] -->|TLS reverse proxy| R
    M[Aggregate metrics] -.-> R
```

## Trust and data boundaries

- Videos needing conversion or compression are processed by FFmpeg WebAssembly
  in the browser; their originals are not uploaded. Fitting MP4s may bypass
  recompression and be uploaded unchanged to the relay for Discord delivery.
- Finished MP4 files cross the HTTPS boundary and are held briefly in process
  memory while awaiting Discord delivery.
- The service does not write uploaded files to disk.
- Discord stores the final message attachments according to Discord's own
  policies.
- Session tokens are high entropy, short-lived, and single-use. Opening a link
  replaces it with a separate browser-bound claim secret.

## Request lifecycle

1. `/compress` creates an expiring upload job tied to the requesting user and
   channel.
2. The browser claims the one-use link and receives the compressor interface.
3. The browser checks file signatures and compresses selected videos locally.
4. Completed MP4 files are uploaded as one multipart batch with automatic
   retry.
5. The relay verifies MP4 structure and enforces file, batch, concurrency,
   memory, and rate limits.
6. A bounded worker queue sends every result in one Discord message and then
   releases the in-memory bytes.

## Operational model

The current deployment is intentionally single-process and uses in-memory
state. This keeps the small deployment simple, but it means sessions, queue
contents, and aggregate metrics disappear on restart. Horizontal scaling would
require shared session storage and a durable external queue.

`/healthz` reports liveness, release revision, and current queue pressure.
`/readyz` checks Discord, command synchronization, workers, and maintenance state.
`/metricsz` reports
process-lifetime aggregate usage counters. Metrics never include user IDs,
guild IDs, channel IDs, filenames, IP addresses, or file contents.

## Code boundaries

The runtime is organized as a Python package with intentionally narrow module
responsibilities:

- `config.py` is the only environment parsing boundary. It validates numbers,
  URLs, and Discord IDs, and prevents secret values from appearing in object
  representations.
- `domain.py` defines upload jobs, delivery requests, browser results, and the
  explicit job-state machine.
- `application.py` integrates Discord and aiohttp and owns process lifecycle.
- `web_ui.py` renders the browser compression client and serializes dynamic
  values safely into JavaScript.
- `media_validation.py` validates untrusted MP4 structure without trusting
  filenames or MIME headers.
- `notifications.py` formats operator messages with escaped Discord usernames
  and user IDs, but without channel names, filenames, IP addresses, session
  tokens, or file content.
- `metrics.py` provides locked, aggregate, process-lifetime counters.

The package root has no import-time startup behavior. The runtime token is
required only when the entry point starts the Discord client, so individual
components can be imported and tested without production credentials.

## Failure and overload behavior

The relay uses bounded concurrency, queue depth, and in-memory byte limits.
When any limit is reached, it returns a retryable response instead of accepting
unbounded work. A delivery request has one terminal success or failure result;
the worker releases its accounted bytes in a `finally` block. Browser failure
reports are authenticated with the claimed session secret and deduplicated
before they generate an operator alert. A browser-side failure does not consume
the session, so the page's retry action remains usable until the session
expires.

## Delivery recovery and expiration

The target is the normal bot-message allowance, not a user's Nitro interaction
allowance. The command checks effective channel permissions before creating a
link, and the worker checks permissions and the current bot allowance again
before sending. Threads have separate send permissions.

A HEAD inspection never claims a link. A GET still claims the one-use page;
refreshing it does not open another session. During active encoding, authenticated
heartbeats renew the browser lease up to a two-hour total lifetime (or a longer
explicitly configured active-session lifetime). Idle sessions expire normally.
A second command cannot replace a session actively encoding or delivering;
abandoned encoding leases clear after 90 seconds without a heartbeat.

An accepted delivery is owned by the queue, not the browser connection. The HTTP
request waits briefly, then returns 202 if work is pending. The browser polls an
authenticated status endpoint. Queued work is not deleted by browser-session
expiry, and disconnecting does not cancel the worker. A lost success response is
recovered from a bounded one-hour receipt cache; duplicate uploads return that
receipt without sending another message. Receipts contain no media or filenames.
After receipt expiry or a process restart, a user must check Discord before
starting another session. Exactly-once delivery across process failure or an
ambiguous Discord API timeout is not guaranteed.

Prepared browser files survive recoverable upload failures until the page closes
or succeeds. Send-only retries use those files; optional local download links
allow recovery without another encoding pass. Nothing is downloaded automatically.

## Shutdown

HTTP starts independently of Discord command synchronization. Transient sync
failures retry while readiness stays false. On SIGTERM/SIGINT the process stops
accepting new sessions and drains active encoding leases, uploads, and deliveries
for at most 120 seconds. The configured container stop grace period is longer.
This reduces interruptions but is not persistent storage or zero-downtime deployment.
