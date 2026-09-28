"""Public legal pages for the hosted Professor Compressor service."""

from html import escape

EFFECTIVE_DATE = "September 27, 2026"
PROJECT_URL = "https://github.com/Anthony-Rama/professor-compressor"


def _document(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(title)} for Professor Compressor">
  <title>Professor Compressor | {escape(title)}</title>
  <link rel="icon" type="image/png" href="/brand/professor-compressor.png">
  <link rel="apple-touch-icon" href="/brand/professor-compressor.png">
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui,
             -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #090e18; color: #f8fafc; }}
    main {{ width: min(820px, calc(100% - 32px)); margin: 40px auto;
            padding: clamp(24px, 5vw, 52px); border: 1px solid #2b3a50;
            border-radius: 22px; background: #172131;
            box-shadow: 0 24px 70px rgba(0, 0, 0, .35); }}
    header {{ padding-bottom: 24px; border-bottom: 1px solid #344158; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(32px, 7vw, 48px);
          letter-spacing: -.035em; }}
    h2 {{ margin: 34px 0 10px; font-size: 21px; }}
    p, li {{ color: #bdc7d5; line-height: 1.65; }}
    li + li {{ margin-top: 8px; }}
    a {{ color: #aeb4ff; }}
    .effective {{ margin: 0; color: #8f9db0; }}
    .notice {{ margin-top: 24px; padding: 15px 17px; border: 1px solid #41506a;
               border-radius: 12px; background: #111a2a; }}
    footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #344158;
              color: #8f9db0; font-size: 14px; }}
    footer a + a {{ margin-left: 18px; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{escape(title)}</h1>
      <p class="effective">Effective date: {EFFECTIVE_DATE}</p>
    </header>
    {content}
    <footer>
      <a href="/privacy">Privacy Policy</a>
      <a href="/terms">Terms of Service</a>
      <a href="/brand/third-party-notices.txt">Third-party notices</a>
      <a href="{PROJECT_URL}">Source code</a>
    </footer>
  </main>
</body>
</html>"""


def privacy_policy_html() -> str:
    return _document(
        "Privacy Policy",
        """
    <p class="notice"><strong>Summary:</strong> Professor Compressor processes
      videos locally in your browser. Finished files, including fitting MP4s
      sent without recompression, pass through the relay to Discord. The relay does
      not write those files to disk.</p>

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
      <li><strong>Security data:</strong> an IP address is processed temporarily
        in memory to enforce request-rate limits and protect the Service from
        abuse.</li>
      <li><strong>Operational data:</strong> aggregate process counters may track
        sessions, queued files, delivered bytes, failures, and rate-limited
        requests. Installation notifications sent privately to the operator
        may include a server name, server identifier, member count, and totals.
        Private compression-session and outcome notifications also include the
        command user's Discord username and user ID so the operator can
        correlate a session with a delivery issue. These alerts do not include
        filenames or video files.</li>
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
      Professor Compressor does not intentionally write video files to disk.</p>
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

    <h2>7. Cookies and tracking</h2>
    <p>The compressor does not use advertising trackers, third-party analytics,
      or browser cookies. A random, short-lived session secret is used to
      authorize delivery retries from the browser that opened the private link.</p>

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
      personal-information database. Operator alerts and feedback emails may
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
