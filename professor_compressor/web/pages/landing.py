"""Permanent public pages for product discovery."""

import json
from html import escape

from ...branding import (
    BOT_INVITE_URL,
    DISCORD_BOT_LIST_URL,
    DISCORD_BOTS_URL,
    DSC_LISTING_URL,
    TOPGG_LISTING_URL,
)
from ...paths import TEMPLATES
from .site import (
    GUIDE_PATH,
    SITE_CSS,
    SUPPORT_EMAIL_URL,
    SUPPORT_SERVER_URL,
    site_footer,
    site_header,
)


def _document(
    base_url: str,
    title: str,
    description: str,
    body: str,
    path: str,
    *,
    current: str = "",
) -> str:
    canonical = f"{base_url}{path}"
    image = f"{base_url}/brand/professor-compressor.png"
    app_data: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Professor Compressor",
        "url": base_url,
        "description": (
            "A Discord bot that prepares oversized videos in your browser "
            "and delivers MP4 files to your Discord channel."
        ),
        "applicationCategory": "MultimediaApplication",
        "operatingSystem": "Web browser",
        "image": image,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
    }
    if path == "/":
        website_data = {
            "@type": "WebSite",
            "name": "Professor Compressor",
            "url": f"{base_url}/",
        }
        app_data.pop("@context")
        schema: dict[str, object] = {
            "@context": "https://schema.org",
            "@graph": [website_data, app_data],
        }
    else:
        schema = app_data
    structured_data = json.dumps(schema, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <link rel="icon" type="image/png" href="/brand/professor-compressor.png">
  <link rel="apple-touch-icon" href="/brand/professor-compressor.png">
  <meta name="description" content="{escape(description, quote=True)}">
  <link rel="canonical" href="{escape(canonical, quote=True)}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Professor Compressor">
  <meta property="og:title" content="{escape(title, quote=True)}">
  <meta property="og:description" content="{escape(description, quote=True)}">
  <meta property="og:url" content="{escape(canonical, quote=True)}">
  <meta property="og:image" content="{escape(image, quote=True)}">
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{escape(title, quote=True)}">
  <meta name="twitter:description" content="{escape(description, quote=True)}">
  <meta name="twitter:image" content="{escape(image, quote=True)}">
  <script type="application/ld+json">{structured_data}</script>
  <meta name="color-scheme" content="dark light">
  <style>
{SITE_CSS}
{_LANDING_CSS}
  </style>
</head>
<body>
  {site_header(current)}
  <main>{body}</main>
  {site_footer()}
</body>
</html>"""


_LANDING_CSS = (TEMPLATES / "landing.css").read_text(encoding="utf-8")


def home_html(base_url: str) -> str:
    description = (
        "Compress oversized videos and clips in your browser, then send "
        "Discord-ready MP4 files back to your channel with Professor Compressor."
    )
    invite_url = escape(BOT_INVITE_URL, quote=True)
    body = f"""
  <section class="hero"><div class="wrap hero-grid">
    <div class="hero-copy">
      <span class="eyebrow">Free Discord video compressor bot</span>
      <h1>Compress videos for Discord</h1>
      <p class="lead">Share clips that are over your server's upload limit. Run
        <span class="command">/compress</span>, add up to 10 videos, and get MP4s
        back in the same channel.</p>
      <div class="actions">
        <a class="btn btn-primary btn-lg" href="{invite_url}">Add to Discord</a>
        <a class="btn btn-outline btn-lg" href="#how">See how it works</a>
      </div>
    </div>
    <div class="preview" aria-hidden="true">
      <div class="preview-head"><img src="/brand/professor-compressor.png" alt=""
        width="40" height="40"><div><strong>Professor Compressor</strong>
        <span>Clips from @you</span></div></div>
      <div class="preview-clips"><span><span class="icon icon-video"></span></span>
        <span><span class="icon icon-video"></span></span></div>
      <div class="preview-sizes"><span>48 MB → 20 MB</span>
        <span>49 MB → 20 MB</span></div>
    </div>
  </div></section>
  <section id="how" class="band band-alt" aria-labelledby="workflow-title">
  <div class="wrap">
    <h2 id="workflow-title" class="section-title">From oversized clip to Discord
      message</h2>
    <ol class="steps">
      <li><span class="step-number" aria-hidden="true">1</span>
        <h3>Run /compress</h3>
        <p>Get a private, expiring link in the channel where you want the
        videos.</p></li>
      <li><span class="step-number" aria-hidden="true">2</span>
        <h3>Choose your videos</h3>
        <p>Open the link and add up to 10 videos. Keep the page open while it
        works.</p></li>
      <li><span class="step-number" aria-hidden="true">3</span>
        <h3>Get the MP4s</h3>
        <p>The bot posts the finished files together in your original
        channel.</p></li>
    </ol>
  </div></section>
  <section class="band" aria-labelledby="features-title"><div class="wrap">
    <h2 id="features-title" class="section-title">Made for Discord's upload limit</h2>
    <div class="features">
      <div><h3>Processed on your device</h3><p>Videos that need converting are
        compressed in your browser.</p></div>
      <div><h3>Automatic size target</h3><p>Aims just below the upload limit
        Discord reports for your server.</p></div>
      <div><h3>Clear progress</h3><p>See each video's progress, elapsed time, and
        finished size.</p></div>
      <div><h3>Works with common formats</h3><p>MP4, MOV, WebM, MKV, AVI, MPEG and
        more. Codec support varies.</p></div>
      <div><h3>One private session</h3><p>Links expire and open once. Each session
        takes up to 10 videos.</p></div>
      <div><h3>Delivered for you</h3><p>No downloading and re-uploading. Finished
        MP4s go straight to Discord.</p></div>
    </div>
  </div></section>
  <section class="band band-alt" aria-labelledby="privacy-title">
  <div class="wrap split">
    <h2 id="privacy-title" class="section-title">What happens to your files?</h2>
    <div class="split-body"><p>Videos that need compression are processed inside
      your browser. Only finished MP4 files pass through the encrypted relay
      to Discord. An MP4 that already fits may be sent unchanged, so its
      original bytes can pass through the relay. The relay holds files briefly
      in memory and does not intentionally save them to disk.</p>
      <a href="/privacy">Read the Privacy Policy</a></div>
  </div></section>
  <section class="band" aria-labelledby="faq-title"><div class="wrap">
    <h2 id="faq-title" class="section-title">Common questions</h2>
    <div class="faq">
      <details open><summary>How do I compress a video for Discord?</summary>
        <p>Add the bot to your server, run <strong>/compress</strong>, open the link,
        and select your video. The finished MP4 is posted in the same channel.
        <a href="{GUIDE_PATH}">Read the step-by-step guide.</a></p></details>
      <details><summary>Do I need Nitro or other software?</summary>
        <p>No. It uses your server's upload limit and runs in a modern browser.
        It's free to use.</p></details>
      <details><summary>Can I compress several clips at once?</summary>
        <p>Yes. You can select or drag and drop up to 10 videos in one session.
        Processing time depends on your device, browser, and video
        lengths.</p></details>
      <details><summary>Will every video format work?</summary>
        <p>The page accepts many common containers, but conversion also depends on
        the codec inside each file. Encrypted, damaged, and AV1 sources may not
        convert with the current encoder.</p></details>
      <details><summary>Does it work on mobile?</summary>
        <p>A modern desktop browser is recommended. Browser memory and background
        processing limits can interrupt large videos on mobile devices.</p></details>
    </div>
  </div></section>
  <section class="band" aria-labelledby="start-title"><div class="wrap">
    <div class="cta">
      <div><h2 id="start-title">Ready to share your clip?</h2>
        <p>Add Professor Compressor to a server where you can manage apps, then
        run <strong>/compress</strong> in a channel.</p></div>
      <a class="btn btn-primary btn-lg" href="{invite_url}">Add to Discord</a>
    </div>
  </div></section>
  <section class="band band-alt" aria-label="Support and listings">
  <div class="wrap help-grid">
    <section id="support" aria-labelledby="support-title">
      <h2 id="support-title">Questions or concerns?</h2>
      <p>Email us or join the official Professor Compressor Discord server
        for help and feedback. For private issues, use email rather than a
        public server channel.</p>
      <div class="link-row">
        <a href="{SUPPORT_EMAIL_URL}">Email support</a>
        <a href="{SUPPORT_SERVER_URL}">Join the support server</a>
      </div>
    </section>
    <section id="listings" aria-labelledby="listings-title">
      <h2 id="listings-title">Find Professor Compressor</h2>
      <p>Professor Compressor is listed in these Discord bot directories:</p>
      <div class="link-row">
        <a href="{DSC_LISTING_URL}">dsc.sh</a>
        <a href="{DISCORD_BOT_LIST_URL}">Discord Bot List</a>
        <a href="{DISCORD_BOTS_URL}">discord.bots.gg</a>
        <a href="{TOPGG_LISTING_URL}">Top.gg</a>
      </div>
    </section>
  </div></section>"""
    return _document(
        base_url,
        "Professor Compressor | Discord Video Compressor Bot",
        description,
        body,
        "/",
    )


def guide_html(base_url: str) -> str:
    description = (
        "Learn how to compress a video for Discord when the file is too large. "
        "Use Professor Compressor to process clips locally and deliver MP4s "
        "to your channel."
    )
    invite_url = escape(BOT_INVITE_URL, quote=True)
    body = f"""
  <div class="wrap guide-layout">
  <nav class="toc" aria-label="On this page">
    <strong>On this page</strong>
    <a href="#steps">Five steps</a>
    <a href="#size">How the size target works</a>
    <a href="#formats">Formats and privacy</a>
    <a href="#slow">If compression is slow</a>
    <a href="#trouble">Troubleshooting</a>
  </nav>
  <article class="guide">
    <span class="eyebrow">Step-by-step guide</span>
    <h1>How to compress a video for Discord</h1>
    <p class="lead">If Discord says your clip is too large, the file needs to
      fit the upload limit for that server. Professor Compressor reads the limit
      Discord reports and prepares an MP4 for that channel.</p>
    <h2 id="steps">Share a large video in five steps</h2>
    <ol class="guide-steps">
      <li><span><a href="{invite_url}">Add Professor Compressor</a>
        to your server.</span></li>
      <li><span>Run <strong>/compress</strong> in the channel where you want the
        video.</span></li>
      <li><span>Open the private link and select or drag in up to 10 files.</span></li>
      <li><span>Keep the browser page open and active while the files are
        processed.</span></li>
      <li><span>Wait for the bot to deliver the finished MP4 files in that
        channel.</span></li>
    </ol>
    <h2 id="size">How the size target works</h2>
    <p>The bot uses the attachment limit Discord reports for your server and
      leaves a little room below it for the final MP4. A file that already
      fits may be sent unchanged. Larger videos are encoded locally in your
      browser. Very long videos may need substantial quality reduction to fit,
      and some files cannot be converted.</p>
    <h2 id="formats">Formats and privacy</h2>
    <p>Common inputs include MP4, MOV, WebM, MKV, AVI, MPEG, WMV, and FLV.
      The codec inside the file also matters. AV1, damaged, encrypted, or
      audio-only inputs may fail with the current encoder.</p>
    <p>Video encoding happens on your device. Finished MP4 files travel over
      HTTPS through the relay to Discord. If an original MP4 already fits,
      it may travel through the relay unchanged. The relay does not intentionally
      store files on disk. See the <a href="/privacy">Privacy Policy</a>
      for details.</p>
    <h2 id="slow">Tips if compression is slow</h2>
    <p>Use a modern desktop browser, keep the page open and visible, and try a
      shorter clip or fewer files at once. Large and long videos use more
      memory and processing time. The page shows progress for each file.</p>
    <h2 id="trouble">When something does not work</h2>
    <div class="trouble"><h3>The private link says it expired</h3>
    <p>Each link is short-lived and can only be opened once. Return to Discord
      and run <strong>/compress</strong> again to get a new one.</p></div>
    <div class="trouble"><h3>A video cannot be converted</h3>
    <p>A file extension does not guarantee that its video codec is supported.
      Try an MP4 with a common video codec, or test one shorter clip first.
      Damaged, encrypted, and AV1 sources may not work with the current
      browser encoder.</p></div>
    <div class="trouble"><h3>The finished file does not arrive in Discord</h3>
    <p>Wait until the page reports delivery complete. If it reports a failure,
      check that the bot is still in the server and can access the original
      channel, then start a new session. A completed upload is not the same as
      a delivered Discord message.</p></div>
    <div class="cta"><div><h3>Start with one command</h3>
      <p>No separate compressor installation or manual upload of the result is
        needed after adding the bot.</p></div>
      <a class="btn btn-primary btn-lg" href="{invite_url}">Add to Discord</a>
    </div>
  </article>
  </div>"""
    return _document(
        base_url,
        "Professor Compressor | How to Compress Videos for Discord",
        description,
        body,
        GUIDE_PATH,
        current="guide",
    )


def robots_txt(base_url: str) -> str:
    return f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n"


def sitemap_xml(base_url: str) -> str:
    urls = ("/", GUIDE_PATH, "/privacy", "/terms")
    entries = "\n".join(
        f"  <url><loc>{escape(base_url + path)}</loc></url>" for path in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n</urlset>\n"
    )
