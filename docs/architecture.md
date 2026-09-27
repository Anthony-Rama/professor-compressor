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

`/healthz` reports readiness and current queue pressure. `/metricsz` reports
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
