# Pre-merge audit — 2026-09-26

Branch: `public-release-overhaul`. These results cover the working tree, not a
deployment. No merge or production deployment was performed during this audit.

## Confirmed defects corrected

- The content security policy blocked WebAssembly compilation. Real Chrome
  reproduced failure of both encoder variants; the corrected policy permits
  WebAssembly without broadly enabling JavaScript eval.
- Small QuickTime MOV files could bypass conversion and then fail MP4 validation.
- Cancellation could re-enable submission before an earlier attempt settled.
  Metadata reads now have cancellation and timeout handling as well.
- Receiving uploads were not included in the shared memory budget. Reservations
  now happen during receipt and are released on rejection or cancellation.
- Delivery workers retained the previous batch while waiting for the next job.
- Upload reads and Discord delivery lacked bounded operation timeouts.
- Non-ASCII session headers and non-object failure JSON could cause server errors.
- Default HTTP access logging could record private one-use session URLs.
- Privacy copy omitted the fact that fitting MP4 files can be sent unchanged.

## Verification results

- 44 Python tests passed, covering configuration, validation, routes, packaged
  assets, notifications, authorization, delivery, limits, and resource cleanup.
- 10 browser tests passed in local Chrome with generated video fixtures:
  actual oversized-video compression under CSP, single-thread fallback,
  ten-file passthrough, MOV conversion, invalid-input recovery, cancellation,
  consumed-link rejection, busy-relay retry, delivery rejection, and narrow layout.
- Python lint, formatting, compilation, and Git whitespace checks passed.
- Python dependency audit and npm audit reported no known vulnerabilities.
- A targeted scan of tracked files found no webhook credentials, DiscordThings
  tokens, or private-key headers. This is not proof that every secret is absent.
- Browser tests and a production-container HTTP/assets smoke test are included in
  CI. Check the latest branch run before merging; local results alone are not a
  substitute for that run.

## Release gates still requiring verification

- Run an actual `/compress` against the candidate deployment and confirm both
  attachments and the private success notification arrive. Repeat with a denied
  attachment permission and verify the failure notification and user message.
- Verify `/botstats` visibility and authorization using a non-owner account.
- Verify live server-install/remove notifications and dsc.sh statistics reporting.
- Require the production-container CI checks to pass, then verify HTTPS, health
  endpoints, and browser assets behind the deployed proxy after deployment.
- Test Edge, Firefox, Safari, low-memory devices, long clips, and realistic
  concurrent workloads. The Chrome tests do not establish support for every
  browser, codec, file size, or hardware configuration.

The browser harness replaces only the Discord delivery boundary. It validates
actual compressed output but cannot certify live Discord permissions, outages,
rate limits, or third-party notification endpoints. No audit proves zero bugs.
