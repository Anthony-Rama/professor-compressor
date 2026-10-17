# Processing diagnostics

Completion alerts retain the existing session elapsed time and add encoding-only
time, engine loading time/mode, time hidden before upload, and relay receive,
queue, and Discord delivery times. Long batch alerts summarize files to stay
within Discord's 2,000-character message limit; structured logs retain details.

`Processing diagnostics` is the server-observed delivery outcome. Its files include
processing path, encoding attempts/time, clip duration, encoding speed (clip
seconds / encoding seconds), sizes, format, bitrate and available resolution/FPS.
`Browser processing diagnostics` is a best-effort client report with the same
random batch ID, total time from Compress to completion/failure, cumulative upload
transfer time, hidden time, retry counts, sanitized error category and whether a
failed attempt was subsequently recovered. Client measurements are untrusted,
bounded diagnostics and never control upload limits or delivery decisions.

- Encoding time includes every encoder attempt, including failures and size
  corrections. It excludes loading, remuxing, probes, file copies and network I/O.
- Browser upload time sums XHR upload intervals, including failed transfers.
  Server receive time measures the accepted request only. Neither is Discord time.
- Queue time is measured separately; Discord delivery time includes destination
  checks and the delivery call. Client total time can also include retry backoff
  and time waiting for the user to manually retry the same prepared batch.
- Hidden time covers active processing/delivery, not idle manual-retry waiting.
  It is context for possible throttling, not proof of its cause.
- Files passed through unchanged do not load the encoder or probe just to collect
  metadata. Their resolution/FPS remain unknown. Remuxed files retain input values.
  Encoded output metadata uses a bounded header read of the existing virtual file,
  with no extra encoding pass or video copy. Missing metadata cannot fail delivery.
- Browser reports can be lost when a page closes or the network fails. Cancellation
  is reported separately from failure. Missing telemetry is not proof of a closed tab.
- New structured records contain no captions, filenames, session links, Discord
  names/IDs or raw exception messages. Existing private owner alerts retain their
  existing identity fields. The random batch ID only joins this batch's records.

Use encoding speed together with resolution/FPS and engine mode when comparing
devices. Use successful server deliveries divided by attempts, failure categories,
and recovered retries to judge reliability; do not count cancellations as errors.
