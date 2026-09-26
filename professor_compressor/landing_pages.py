"""Permanent public pages for product discovery."""

import json
from html import escape

BOT_INVITE_URL = (
    "https://discord.com/oauth2/authorize?client_id=1550752400271351839"
    "&permissions=277025426432&integration_type=0&scope=bot+applications.commands"
)
PROJECT_URL = "https://github.com/Anthony-Rama/professor-compressor"
TOP_GG_URL = "https://top.gg/discord/bots/1550752400271351839"
GUIDE_PATH = "/guide/compress-video-for-discord"


def _document(base_url: str, title: str, description: str, body: str, path: str) -> str:
    canonical = f"{base_url}{path}"
    image = f"{base_url}/brand/professor-compressor.png"
    app_data = {
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
    structured_data = json.dumps(app_data, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
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
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui,
             -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; color: #eef4ff; background: #080e1c;
            line-height: 1.6; }}
    a {{ color: #73e4f5; }}
    a:hover {{ color: #b6f5fc; }}
    .wrap {{ width: min(1080px, calc(100% - 40px)); margin: auto; }}
    header {{ border-bottom: 1px solid #29364f; }}
    .nav {{ display: flex; align-items: center; justify-content: space-between;
            gap: 20px; min-height: 82px; }}
    .wordmark {{ display: inline-flex; align-items: center; gap: 12px;
                 color: #eef4ff; font-weight: 800; text-decoration: none; }}
    .wordmark img {{ width: 44px; height: 44px; border-radius: 12px; }}
    nav {{ display: flex; flex-wrap: wrap; gap: 18px; font-size: 14px; }}
    .hero {{ display: grid; grid-template-columns: 1.3fr .7fr; gap: 52px;
             align-items: center; padding: 86px 0 70px; }}
    .eyebrow {{ color: #73e4f5; font-size: 13px; font-weight: 800;
                letter-spacing: .13em; text-transform: uppercase; }}
    h1 {{ font-size: clamp(38px, 6vw, 70px); line-height: 1.08;
          letter-spacing: -.055em; margin: 14px 0 22px; }}
    h2 {{ font-size: clamp(26px, 3vw, 36px); line-height: 1.2;
          letter-spacing: -.03em; margin: 0 0 18px; }}
    h3 {{ margin: 0 0 8px; font-size: 18px; }}
    p, li {{ color: #b7c5da; }}
    .lead {{ font-size: 18px; max-width: 660px; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 30px; }}
    .button {{ display: inline-flex; align-items: center; justify-content: center;
               min-height: 48px; padding: 11px 19px; border-radius: 10px;
               font-weight: 800; text-decoration: none; }}
    .button.primary {{ background: #35d5ec; color: #091324; }}
    .button.secondary {{ border: 1px solid #52617b; color: #eef4ff; }}
    .mascot {{ width: min(290px, 100%); justify-self: center; border-radius: 54px;
               box-shadow: 0 24px 80px #1d48d45c; }}
    section {{ padding: 56px 0; border-top: 1px solid #29364f; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 15px; }}
    .card {{ padding: 23px; background: #131f35; border: 1px solid #33445e;
             border-radius: 16px; }}
    .card p {{ margin-bottom: 0; }}
    .steps {{ counter-reset: steps; }}
    .steps .card::before {{ counter-increment: steps; content: counter(steps);
                            display: grid; place-items: center; width: 32px;
                            height: 32px; margin-bottom: 20px; border-radius: 8px;
                            background: #1c5967; color: #b6f5fc; font-weight: 800; }}
    .callout {{ padding: 23px; background: #172d3a; border: 1px solid #386678;
                border-radius: 16px; }}
    .faq {{ max-width: 820px; }}
    .faq h3 {{ margin-top: 28px; }}
    .guide {{ max-width: 810px; padding: 66px 0 90px; }}
    .guide h1 {{ font-size: clamp(34px, 5vw, 54px); }}
    .guide h2 {{ margin-top: 48px; }}
    .guide ol li {{ padding-left: 6px; margin-top: 10px; }}
    footer {{ border-top: 1px solid #29364f; padding: 32px 0 45px;
              font-size: 13px; }}
    .footer-links {{ display: flex; flex-wrap: wrap; gap: 18px; }}
    @media (max-width: 720px) {{
      .hero {{ grid-template-columns: 1fr; padding: 56px 0; gap: 42px; }}
      .mascot {{ width: 180px; justify-self: start; border-radius: 34px; }}
      .grid {{ grid-template-columns: 1fr; }}
      .nav {{ align-items: flex-start; padding: 16px 0; flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <header><div class="wrap nav">
    <a class="wordmark" href="/"><img src="/brand/professor-compressor.png"
      alt="" width="44" height="44">Professor Compressor</a>
    <nav aria-label="Main navigation">
      <a href="{GUIDE_PATH}">Written instructions</a>
      <a href="{PROJECT_URL}">GitHub</a>
      <a href="{TOP_GG_URL}">Top.gg</a>
    </nav>
  </div></header>
  <main class="wrap">{body}</main>
  <footer><div class="wrap footer-links">
    <a href="/privacy">Privacy Policy</a>
    <a href="/terms">Terms of Service</a>
    <a href="/brand/third-party-notices.txt">Third-party notices</a>
    <a href="{PROJECT_URL}">Source and contact</a>
    <span>Professor Compressor is not affiliated with Discord.</span>
  </div></footer>
</body>
</html>"""


def home_html(base_url: str) -> str:
    description = (
        "Compress oversized videos and clips in your browser, then send "
        "Discord-ready MP4 files back to your channel with Professor Compressor."
    )
    invite_url = escape(BOT_INVITE_URL, quote=True)
    body = f"""
  <div class="hero">
    <div>
      <div class="eyebrow">Free Discord video compressor bot</div>
      <h1>Compress videos for Discord</h1>
      <p class="lead">Professor Compressor helps you share clips that exceed
        your server's upload limit. Run <strong>/compress</strong>, select or
        drag in up to 10 videos, and receive MP4 files in the same channel.</p>
      <div class="actions">
        <a class="button primary" href="{invite_url}">Add to Discord</a>
        <a class="button secondary" href="{GUIDE_PATH}">Read how it works</a>
      </div>
    </div>
    <img class="mascot" src="/brand/professor-compressor.png"
      alt="Professor Compressor mascot holding a zipped video file"
      width="290" height="290">
  </div>
  <section aria-labelledby="workflow-title">
    <h2 id="workflow-title">From oversized clip to Discord message</h2>
    <div class="grid steps">
      <div class="card"><h3>Run /compress</h3><p>Get a private, expiring link
        in the Discord channel where you want the videos sent.</p></div>
      <div class="card"><h3>Choose your videos</h3><p>Open the link and select
        or drop up to 10 videos. Keep the page open while it works.</p></div>
      <div class="card"><h3>Get the MP4s</h3><p>The bot sends the finished
        files together in a message in your original channel.</p></div>
    </div>
  </section>
  <section aria-labelledby="features-title">
    <h2 id="features-title">Made for Discord's upload limit</h2>
    <div class="grid">
      <div class="card"><h3>Local video processing</h3><p>Videos needing
        conversion are processed on your device in the browser.</p></div>
      <div class="card"><h3>Automatic size target</h3><p>The compressor aims
        below the upload limit Discord reports for your server.</p></div>
      <div class="card"><h3>Useful progress</h3><p>See progress for each
        video, elapsed time, and finished file sizes.</p></div>
      <div class="card"><h3>Broad input support</h3><p>Choose MP4, MOV,
        WebM, MKV, AVI, MPEG, and other common containers.
        Codec support varies.</p></div>
      <div class="card"><h3>One private session</h3><p>Links expire and can
        only be opened once; each session accepts up to 10 videos.</p></div>
      <div class="card"><h3>Automatic delivery</h3><p>No manual download and
        re-upload step. Completed MP4s return to Discord.</p></div>
    </div>
  </section>
  <section aria-labelledby="privacy-title">
    <h2 id="privacy-title">What happens to your files?</h2>
    <div class="callout"><p>Videos that need compression are processed inside
      your browser. Only finished MP4 files pass through the encrypted relay
      to Discord. An MP4 that already fits may be sent unchanged, so its
      original bytes can pass through the relay. The relay holds files briefly
      in memory and does not intentionally save them to disk.</p>
      <a href="/privacy">Read the Privacy Policy</a></div>
  </section>
  <section class="faq" aria-labelledby="faq-title">
    <h2 id="faq-title">Common questions</h2>
    <h3>How do I compress a video for Discord?</h3>
    <p>Add the bot to your server, run <strong>/compress</strong>, open the link,
      and select your video. The finished MP4 is posted in the same channel.
      <a href="{GUIDE_PATH}">Read the step-by-step guide.</a></p>
    <h3>Do I need Nitro or separate compression software?</h3>
    <p>No. The bot uses your server's reported upload limit and runs video
      processing in a modern browser. The service is free to use.</p>
    <h3>Can I compress several clips at once?</h3>
    <p>Yes. You can select or drag and drop up to 10 videos in one session.
      Processing time depends on your device, browser, and video lengths.</p>
    <h3>Will every video format work?</h3>
    <p>The page accepts many common containers, but conversion also depends on
      the codec inside each file. Encrypted, damaged, and AV1 sources may not
      convert with the current encoder.</p>
    <h3>Does it work on mobile?</h3>
    <p>A modern desktop browser is recommended. Browser memory and background
      processing limits can interrupt large videos on mobile devices.</p>
  </section>
  <section aria-labelledby="start-title">
    <h2 id="start-title">Ready to share your clip?</h2>
    <p>Add Professor Compressor to a server where you can manage apps, then
      run <strong>/compress</strong> in a channel.</p>
    <a class="button primary" href="{invite_url}">Add to Discord</a>
  </section>"""
    return _document(
        base_url,
        "Discord Video Compressor Bot | Professor Compressor",
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
  <article class="guide">
    <div class="eyebrow">Step-by-step guide</div>
    <h1>How to compress a video for Discord</h1>
    <p class="lead">If Discord says your clip is too large, the file needs to
      fit the upload limit for that server. Professor Compressor reads the limit
      Discord reports and prepares an MP4 for that channel.</p>
    <h2>Share a large video in five steps</h2>
    <ol>
      <li><a href="{invite_url}">Add Professor Compressor</a>
        to your server.</li>
      <li>Run <strong>/compress</strong> in the channel where you want the video.</li>
      <li>Open the private link and select or drag in up to 10 files.</li>
      <li>Keep the browser page open and active while the files are processed.</li>
      <li>Wait for the bot to deliver the finished MP4 files in that channel.</li>
    </ol>
    <h2>How the size target works</h2>
    <p>The bot uses the attachment limit Discord reports for your server and
      leaves a little room below it for the final MP4. A file that already
      fits may be sent unchanged. Larger videos are encoded locally in your
      browser. Very long videos may need substantial quality reduction to fit,
      and some files cannot be converted.</p>
    <h2>Formats and privacy</h2>
    <p>Common inputs include MP4, MOV, WebM, MKV, AVI, MPEG, WMV, and FLV.
      The codec inside the file also matters. AV1, damaged, encrypted, or
      audio-only inputs may fail with the current encoder.</p>
    <p>Video encoding happens on your device. Finished MP4 files travel over
      HTTPS through the relay to Discord. If an original MP4 already fits,
      it may travel through the relay unchanged. The relay does not intentionally
      store files on disk. See the <a href="/privacy">Privacy Policy</a>
      for details.</p>
    <h2>Tips if compression is slow</h2>
    <p>Use a modern desktop browser, keep the page open and visible, and try a
      shorter clip or fewer files at once. Large and long videos use more
      memory and processing time. The page shows progress for each file.</p>
    <div class="callout"><h3>Start with one command</h3>
      <p>No separate compressor installation or manual upload of the result is
        needed after adding the bot.</p>
      <a class="button primary" href="{invite_url}">Add to Discord</a>
    </div>
  </article>"""
    return _document(
        base_url,
        "How to Compress Videos for Discord | Professor Compressor",
        description,
        body,
        GUIDE_PATH,
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
