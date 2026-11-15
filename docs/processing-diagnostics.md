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
## Recovery behavior

The pinned browser cores recognize AV1 metadata but do not provide a usable
software AV1 decoding path. When re-encoding is required, AV1 is rejected after
metadata inspection with `UNSUPPORTED_CODEC`, without retrying identical
settings. Fitting MP4 passthrough remains unchanged. Revisit this capability
guard when changing the pinned core; a decoder name in `-decoders` alone is not
proof that decoding works in WebAssembly.

Before expensive conversion, the browser checks the same minimum 100 kbps
video budget used for size-targeted encoding. It reserves 96 kbps for audio
only when an audio stream exists. An impossible target produces
`TARGET_NOT_FEASIBLE`; already-fitting MP4s and fitting compatible remuxes are
not rejected by duration alone. This is a feasibility guard, not a perceptual
quality guarantee. No audio is silently removed and no video is trimmed.

Known unsupported codecs, invalid media/signatures and
impossible targets are not automatically re-encoded with identical settings.
Affected unreadable/unsupported files can be reselected or removed without
clearing other selected inputs or the caption.

The relay distinguishes `UPLOAD_CONNECTION_RESET` from generic relay errors.
It records allowlisted received payload-byte count (including caption and
diagnostic field bytes, excluding multipart framing), receive duration,
time since the last payload chunk, and server-counted upload attempts.
It does not store those field contents, raw exceptions or filenames in these
diagnostics. A reset proves interruption, not which peer caused it.

Upload retries are bounded, reconcile receipt before resending, and reuse
prepared output without recompression. Backoff includes jitter unless a bounded
server retry interval is provided. After exhaustion, the user can check/retry
delivery or download prepared files while the page remains open. Browser upload
progress alone does not confirm relay receipt or Discord delivery.

### Delivery certainty and required error handling

Delivery receipts distinguish a confirmed rejection from an unknown outcome.
`DELIVERY_ACCESS_DENIED` covers missing destination access or permissions;
`DELIVERY_LIMIT_CHANGED` means the destination allowance changed after preparation.
`DISCORD_REJECTED` means Discord returned a definitive rejection. Network errors,
server errors and unexpected failures use `DELIVERY_UNCONFIRMED`; a delivery
timeout uses `DELIVERY_TIMEOUT`. Neither unknown category proves that no message
was posted. Users must check the channel before starting another session.
The original page and refreshed status page preserve the same safe recovery
message, and neither automatically resubmits a failed delivery.

`static/processing-errors.js` owns required error normalization/classification.
The optional diagnostics reporter uses the same rules; missing diagnostics must
not change retries or file-replacement actions. Failed required module downloads
produce an independent startup error before files can be selected. Startup also
catches module syntax/evaluation errors and initialization exceptions, and times
out after 30 seconds. A late module response cannot reactivate a timed-out page;
the user must reload. The watchdog and startup error listener are removed after
successful initialization so unrelated later errors do not disable the picker.

User-facing encoding/remux inactivity warnings do not depend on optional
diagnostics. After two minutes without advancing media time or percentage, the
page explains that processing may be slow or paused, not necessarily failed.
Repeated identical progress events do not reset this timer. Returning after at
least 15 seconds in the background shows foreground/screen-unlocked guidance
until progress resumes or the operation ends. Warnings never cancel or retry
processing automatically, and do not change encoding settings or collect new data.
Mobile guidance is shown before starting; long videos are best processed on a
desktop. A hidden page or sleeping device may suspend the warning timers too;
visibility restoration immediately rechecks activity when the browser resumes.

Relay MP4 validation is a bounded structural plausibility check, not a full
decoder or a guarantee that media is playable. The file-type box is limited to
4 KiB and only major/compatible brands are considered; minor-version bytes are
not brands. Header parsing does not materialize an upload-sized brand table.
