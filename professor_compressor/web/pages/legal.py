"""Public legal pages for the hosted Professor Compressor service."""

import re
from html import escape

from ...paths import TEMPLATES
from .site import SITE_CSS, site_footer, site_header
from .theme import THEME_BOOT_SCRIPT, THEME_TOGGLE_LOADER

# Each document keeps its own date; change it only when that document's text changes.
PRIVACY_EFFECTIVE_DATE = "November 26, 2026"
TERMS_EFFECTIVE_DATE = "October 31, 2026"

_SECTION_HEADING = re.compile(r"<h2>((\d+)\. [^<]+)</h2>")

_LEGAL_CSS = (TEMPLATES / "legal.css").read_text(encoding="utf-8")


def _document(title: str, effective_date: str, content: str) -> str:
    sections: list[tuple[str, str]] = []

    def anchor(match: re.Match[str]) -> str:
        sections.append((match.group(2), match.group(1)))
        return f'<h2 id="section-{match.group(2)}">{match.group(1)}</h2>'

    content = _SECTION_HEADING.sub(anchor, content)
    section_links = "\n".join(
        f'        <a href="#section-{number}">{heading}</a>'
        for number, heading in sections
    )
    privacy_current = ' aria-current="page"' if title == "Privacy Policy" else ""
    terms_current = ' aria-current="page"' if title == "Terms of Service" else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {THEME_BOOT_SCRIPT}
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark light">
  <meta name="description" content="{escape(title)} for Professor Compressor">
  <title>Professor Compressor | {escape(title)}</title>
  <link rel="icon" type="image/png" href="/brand/professor-compressor.png">
  <link rel="apple-touch-icon" href="/brand/professor-compressor.png">
  <style>
{SITE_CSS}
{_LEGAL_CSS}
  </style>
</head>
<body>
  {site_header()}
  <div class="wrap legal-layout">
    <aside class="legal-aside">
      <nav class="doc-switch" aria-label="Legal documents">
        <a href="/privacy"{privacy_current}>Privacy</a>
        <a href="/terms"{terms_current}>Terms</a>
      </nav>
      <nav class="sections" aria-label="Sections">
        <strong>Sections</strong>
{section_links}
      </nav>
    </aside>
    <main class="legal">
      <header>
        <h1>{escape(title)}</h1>
        <p class="effective">Effective date: {effective_date}</p>
      </header>
      {content}
    </main>
  </div>
  {site_footer()}
  {THEME_TOGGLE_LOADER}
</body>
</html>"""


def privacy_policy_html() -> str:
    return _document(
        "Privacy Policy",
        PRIVACY_EFFECTIVE_DATE,
        """
    <p class="notice"><strong>Summary:</strong> Professor Compressor processes
      videos locally in your browser. Finished files, including fitting MP4s
      sent without recompression, pass through the relay to Discord. The relay does
      not write those files to server disk. Your browser keeps temporary local
      copies of selected clips while preparing and processing them.</p>

    <h2>1. Scope</h2>
    <p>This policy describes how the hosted Professor Compressor Discord bot and
      browser compressor (the “Service”) process information. It does not govern
      Discord, your browser, or independently operated services.</p>

    <h2>2. Information processed</h2>
    <ul>
      <li><strong>Discord interaction data:</strong> Discord user, server, and
        channel identifiers, interaction metadata, and the applicable Discord
        upload limit are processed to create a session and return results to the
        correct channel.</li>
      <li><strong>Output files:</strong> finished MP4 files, including fitting
        MP4s sent unchanged, and their
        filenames are transmitted over HTTPS and held temporarily in server
        memory while they are validated and delivered to Discord.</li>
      <li><strong>Optional message:</strong> if you add a message for the finished
        videos, its text is held temporarily in server memory and sent with the
        finished files to the original Discord channel. The Service does not
        intentionally write that text to disk; Discord retains the delivered
        message under its own policies.</li>
      <li><strong>Security data:</strong> an IP address is processed temporarily
        in memory to enforce request-rate limits and protect the Service from
        abuse.</li>
      <li><strong>Operational data:</strong> aggregate process counters may track
        sessions, queued files, delivered bytes, failures, and rate-limited
        requests. Installation notifications sent privately to the operator
        may include a server name, server identifier, member count, and totals.
        Private compression-session, browser progress, expiration, and outcome
        notifications also include the command user's Discord username and user ID
        so the operator can correlate a session with a delivery issue.
        Browser progress reports cover link opening, file selection count,
        pressing Compress, cancellation, and page exit or reload. A page-exit
        report is best-effort and may not arrive if the browser or connection
        fails. Outcome alerts may include the detected input container type,
        numeric original and output file sizes, size reduction, detected duration,
        calculated encoding bitrate, encoding time, target utilization, processing
        path, resolution and frame rate, engine mode, retry counts, stage timings,
        and time spent with the page hidden to help
        diagnose compression quality. These alerts do not include
        filenames or video files. When configured, a protected operator dashboard
        stores structured diagnostic events, random session references, keyed
        user/server pseudonyms, browser family and major version, coarse device
        category, and technical media properties, including codec profile, bit
        depth, pixel format, and audio characteristics when available. Diagnostic
        reports may include engine build and requested processing settings,
        recognized failure categories, exit codes, browser features needed for
        processing, attempt numbers, and occasional progress checkpoints. Engine
        messages are classified locally; their raw text is not uploaded as
        diagnostic history. The Service does not collect hardware fingerprints,
        exact available RAM, or browsing history for these reports. It does not
        store raw user agents,
        raw Discord identifiers, filenames, captions, private session links,
        credentials, raw error text, or video contents in that event history.</li>
      <li><strong>Optional feedback:</strong> email feedback and support links
        open your email application. No feedback is sent unless you choose to
        send an email. If you do, the operator receives the email address and
        message you provide. The support-server link opens Discord; anything
        you post there is subject to that server's visibility and Discord's
        policies. Do not share private videos or session links.</li>
    </ul>

    <h2>3. Video processing and storage</h2>
    <p>Videos needing conversion or compression are processed by FFmpeg
      WebAssembly inside your browser; only their resulting MP4 files are
      uploaded. A fitting MP4 may skip recompression, in which case the original
      file is uploaded unchanged through Professor Compressor to Discord.
      Uploaded files are
      held only in volatile memory while awaiting delivery and are released
      after delivery, failure, cancellation, expiration, or a service restart.
      The relay does not intentionally write video files to disk.</p>
    <p>When you select a clip, the browser copies it into temporary, site-private
      storage on your own device so later changes to the exported source do not
      interrupt processing. Copying sends no video bytes to the relay; only
      finished output is uploaded during delivery, including fitting MP4s sent
      unchanged. The page attempts to delete local copies when you remove
      clips, after successful delivery, on idle session expiration, or when you
      leave the page. Closing or crashing the browser can interrupt cleanup;
      a subsequent visit attempts to
      remove abandoned copies without deleting copies owned by another open tab.
      You can also remove local copies by clearing this site's browser data.
      A refresh does not restore your selected clips; reselect them to continue.</p>
    <p>After delivery, Discord stores the resulting message and attachments under
      Discord’s own policies and the settings of the destination server.</p>

    <h2>4. How information is used</h2>
    <p>Information is processed only to operate the compression workflow, route
      files to the requested Discord channel, enforce technical limits, secure
      the Service, diagnose failures, and understand aggregate reliability.</p>

    <h2>5. Sharing</h2>
    <p>Information is shared with Discord when commands are received, operational
      notifications are sent, and completed files are delivered. Infrastructure
      providers may process network traffic as necessary to host and secure the
      Service. Professor Compressor does not sell personal information or use it
      for targeted advertising.</p>
    <p>When configured, bot-directory integrations send the aggregate number of
      connected Discord servers to Top.gg and dsc.sh for public listing statistics.
      These updates do not include server names or identifiers, user information,
      captions, filenames, or video files.</p>

    <h2>6. Retention</h2>
    <p>Unused links expire after a short period, active compressor sessions expire
      automatically, and delivery data is kept in memory only as long as needed
      to complete or fail the request. IP-based rate-limit entries expire after
      their short rate-limit window. Aggregate counters reset when the process
      restarts. To recover an interrupted delivery response without sending files
      twice, the relay keeps a bounded in-memory delivery receipt for up to one
      hour. It contains the session authentication secret, outcome, and file
      count, not video bytes or filenames. Active browser processing can renew
      a session for a bounded period; idle sessions expire normally.
      Discord may retain delivered attachments according to its own
      policies.</p>
    <p>When enabled, structured diagnostic history is stored on the operator's
      server for up to 90 days, subject to row and storage limits. It survives
      bot restarts. It is available only through the protected operator dashboard.
      Exports and backups must be protected and follow the same retention period.
      Keyed pseudonyms are used to measure repeat usage without storing raw
      Discord user/server IDs in this history.</p>

    <h2>7. Cookies, browser storage, and tracking</h2>
    <p>The compressor does not use advertising trackers or third-party analytics.
      It uses one short-lived, first-party, HTTP-only browser cookie to let the
      same browser reopen a claimed link after a refresh and check a delivery
      already in progress. The cookie contains a random secret, not your name
      or video, and expires after at most three hours. The relay also uses a
      separate short-lived page secret to authorize uploads and status checks.</p>
    <p>If you choose the Light or Dark theme, your browser saves that choice in
      this site's local storage under the name <code>pc-theme</code> so other
      Professor Compressor pages use the same theme. The choice stays on your
      device and is never sent to the Service. Choosing System removes it, and
      clearing this site's browser data also deletes it. The temporary local
      copies of selected clips described in section 3 are kept in this site's
      browser storage on your device. Apart from ordinary browser caching of
      the site's files, the Service does not set other cookies or store other
      information in your browser.</p>

    <h2>8. Security</h2>
    <p>The Service uses HTTPS, expiring single-use links, browser-bound session
      secrets, file validation, rate limiting, and bounded in-memory queues.
      No system can guarantee absolute security, and the relay is not end-to-end
      encrypted because it must send the completed files to Discord.</p>

    <h2>9. Your choices</h2>
    <p>You may choose not to use the Service, cancel before uploading completed
      files, remove the bot from a server you manage, decline to send optional
      feedback, or delete delivered files from Discord if you have permission
      to do so. The Service does not maintain user accounts or a persistent
      account database; its configured diagnostic history is described above.
      Operator alerts and feedback emails may
      remain in the operator's Discord channel or mailbox until deleted.</p>

    <h2>10. Age requirements</h2>
    <p>The Service is intended only for people permitted to use Discord under
      Discord’s terms and applicable law.</p>

    <h2>11. Changes and contact</h2>
    <p>This policy may be updated when the Service changes. Material changes will
      be reflected by the effective date above. Questions or privacy concerns
      may be sent by email to
      <a href="mailto:professorcompressor.support@gmail.com">email</a>
      or through the
      <a href="https://discord.com/invite/32RWwNWyEH">support server</a>.
      Do not include private videos, credentials, or session links in
      public channels.</p>
        """,
    )


def terms_of_service_html() -> str:
    return _document(
        "Terms of Service",
        TERMS_EFFECTIVE_DATE,
        """
    <p class="notice">By installing or using Professor Compressor, you agree to
      these Terms. If you do not agree, do not use the Service.</p>

    <h2>1. The Service</h2>
    <p>Professor Compressor provides a Discord command and browser interface that
      compress video files on the user’s device and relay finished MP4 files to
      a Discord channel. The Service currently supports videos only, limits each
      session to the displayed file count and size limits, and may impose
      additional rate, memory, concurrency, or availability limits.</p>

    <h2>2. Eligibility and Discord</h2>
    <p>You must be permitted to use Discord and must comply with Discord’s Terms
      of Service, Community Guidelines, Developer policies where applicable, and
      the rules of each server in which you use Professor Compressor. Professor
      Compressor is not affiliated with or endorsed by Discord Inc.</p>

    <h2>3. Your content</h2>
    <p>You retain your rights in the videos you process. You represent that you
      own the content or have every permission required to process and share it.
      You grant the Service a limited, temporary permission to receive, validate,
      and transmit finished files solely to complete your requested delivery.</p>

    <h2>4. Acceptable use</h2>
    <p>You may not use the Service to:</p>
    <ul>
      <li>process or distribute illegal, infringing, malicious, exploitative, or
        otherwise prohibited content;</li>
      <li>violate another person’s privacy, intellectual-property rights, or the
        rules of Discord or a destination server;</li>
      <li>probe, disrupt, overload, reverse engineer for abuse, or bypass the
        Service’s authentication, file, size, rate, or concurrency controls;</li>
      <li>automate requests in a way that interferes with other users or use the
        Service to distribute malware, spam, or deceptive content.</li>
    </ul>

    <h2>5. Availability and changes</h2>
    <p>The Service is provided on an “as available” basis. Compression speed,
      quality, compatibility, and delivery depend on your hardware, browser,
      network, source files, Discord, and available capacity. Features or limits
      may be modified, suspended, or discontinued at any time.</p>

    <h2>6. Enforcement</h2>
    <p>Access may be rate-limited, suspended, or blocked when reasonably necessary
      to protect the Service, comply with law, respond to Discord, or address a
      violation of these Terms. Server administrators may remove the bot from
      their servers at any time.</p>

    <h2>7. Third-party services</h2>
    <p>The Service relies on Discord and hosting infrastructure. Their services
      and policies are outside Professor Compressor’s control. You are responsible
      for reviewing the terms and privacy practices that apply to those services.</p>

    <h2>8. Disclaimers</h2>
    <p>To the maximum extent permitted by law, the Service is provided without
      warranties of uninterrupted operation, fitness for a particular purpose,
      file compatibility, preservation of quality, or successful delivery. Keep
      your original files and verify completed results before relying on them.</p>

    <h2>9. Limitation of liability</h2>
    <p>To the maximum extent permitted by law, the operator will not be liable for
      indirect, incidental, special, consequential, or punitive damages, or for
      loss of data, content, profits, or access arising from use of or inability
      to use the Service.</p>

    <h2>10. Privacy</h2>
    <p>The <a href="/privacy">Privacy Policy</a> explains how information is
      processed and is incorporated into these Terms.</p>

    <h2>11. Changes and contact</h2>
    <p>These Terms may be updated as the Service evolves. Continued use after an
      update means you accept the revised Terms. Questions may be submitted
      by <a href="mailto:professorcompressor.support@gmail.com">email</a>
      or in the
      <a href="https://discord.com/invite/32RWwNWyEH">support server</a>.</p>
        """,
    )
