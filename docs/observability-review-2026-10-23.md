# Observability branch review — October 23, 2026

Scope: `feature/session-observability`, including its browser instrumentation,
HTTP ingestion, SQLite storage, password-protected dashboard, configuration,
privacy documentation, and deployment files. This is an engineering review and
regression test report, not a formal security certification.

## Findings fixed

| Area | Edge case | Result |
| --- | --- | --- |
| Dashboard access | Failed login traffic could lock out a correct password | Valid credentials remain usable; unsuccessful attempts are rate limited |
| Password encoding | The advertised UTF-8 authentication charset differed from decoding | Credentials are decoded as UTF-8 |
| Retry attribution | More than one queued batch could cross a manual retry boundary | Every queued event retains its original run ID |
| Late failure alerts | An old run's failure could alert during a new run and suppress its later alert | Old reports remain in history without consuming the current run's alert |
| State ordering | A delayed selection report could move processing back to selected | Selection is recorded without regressing the processing state |
| Cancellation | Aborting an engine step could be classified as an encoder failure | Cancelled steps retain their cancellation code and are excluded from failure-stage counts |
| Deduplication | A duplicate failure ID could enrich another event or erase a specific error with UNKNOWN | Enrichment requires the same failure event/run and preserves specific errors |
| Failed file | Errors outside a wrapped operation or reports arriving out of order could lose the file index | Per-file failures retain the index and duplicate reports can supply missing context |
| Dashboard refresh | An older request could overwrite a newer filter result | Only the latest refresh updates the view; stale data cannot be exported during refresh |
| Daily activity | Delivery today was omitted if its session began outside the selected window | Activity uses event timestamps; session summaries retain their session-created window |
| Resource recovery | A failed insert could roll back periodic pruning | Pruning commits before the new insert, allowing reclaimed space to remain available |
| Database readers | Setup errors before the query block could leave a connection open | Reader setup is covered by connection cleanup |
| Browser cookie | Non-ASCII cookie text could cause a comparison TypeError | Invalid cookie values fail comparison without that server error |
| Failure ingestion | Repeated failure requests had no dedicated request rate limit | Authenticated failure reports are limited to 30 per IP per minute |

## Verification

- Python 3.11 backend suite: 137 passing tests.
- Browser suite: 66 passing tests, plus the added refresh-order regression passing
  separately (67 distinct tests). Failure/retry browser paths were rerun after
  the final file-index change.
- Ruff lint and formatting, and `git diff --check`: passing.
- Tests cover authenticated access, payload limits, untrusted browser events,
  privacy filtering, delivery authority, retries, persistence, retention,
  storage failures, supported media paths, and desktop/mobile layouts.
- Existing branch dependency audit passed after the certifi update. The update
  reaches production only when the branch or that patch is released.

## Release checks and remaining limits

Docker is unavailable on this workstation. Before deployment, build the production
image and verify the named volume is writable by the container's non-root user,
persists across a restart, and the configured `/ops` endpoint requires credentials.
Complete one development-server upload and check its timeline and test filtering.
Setup and rollback instructions are in [observability.md](observability.md).

Browser reports remain best-effort, untrusted diagnostic evidence. Missing reports
cannot establish a failure cause, and browser measurements cannot establish visual
quality. A single SQLite writer fits the current one-process deployment; multiple
bot instances would need a separate storage/concurrency design. Host uptime and
disk monitoring, and private backups, remain operational tasks.

The previously discussed crafted-MP4 validation memory amplification issue remains
deferred at the owner's request. This feature does not resolve that existing risk.
Branch protection and reproducible FFmpeg source provenance also remain separate
repository/release improvements. No push, merge, or production deployment was
performed as part of this review.

## Subsequent release verification

On October 23, after release approval, the candidate production image built on
Ubuntu. The container smoke check passed both `seed` and `verify` phases across
container recreation using a dedicated disposable volume. It verified non-root
database ownership/access, mode 0600, authenticated dashboard access, test-server
filtering, sanitized events, and retained session history. The production-image
backend suite also passed (137 tests, one Node-dependent check skipped because
the runtime image intentionally omits Node). That check passed locally.

The repeatable volume check is now included in container CI. These checks resolve
the workstation's Docker-verification limitation described above; production
health and a live Discord acceptance test remain deployment checks.
