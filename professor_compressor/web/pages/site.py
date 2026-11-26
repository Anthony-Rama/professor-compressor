"""Header, footer and styles shared by the public website pages."""

from html import escape

from ...branding import BOT_INVITE_URL, PROJECT_URL
from ...paths import TEMPLATES
from .theme import THEME_CSS

GUIDE_PATH = "/guide/compress-video-for-discord"
SUPPORT_EMAIL_URL = "mailto:professorcompressor.support@gmail.com"
SUPPORT_SERVER_URL = "https://discord.com/invite/32RWwNWyEH"

SITE_CSS = THEME_CSS + "\n" + (TEMPLATES / "site.css").read_text(encoding="utf-8")


def site_header(current: str = "") -> str:
    """The public navigation bar; `current` marks the active page link."""
    invite_url = escape(BOT_INVITE_URL, quote=True)
    guide_current = ' aria-current="page"' if current == "guide" else ""
    return f"""<header class="site-header"><div class="wrap nav">
    <a class="brandmark wordmark" href="/"><img src="/brand/professor-compressor.png"
      alt="" width="38" height="38">Professor Compressor</a>
    <nav class="nav-links" aria-label="Main navigation">
      <a href="{GUIDE_PATH}"{guide_current}>Guide</a>
      <a href="/#support">Support</a>
      <a href="{PROJECT_URL}">GitHub</a>
      <a class="btn btn-primary" href="{invite_url}">Add to Discord</a>
    </nav>
  </div></header>"""


def site_footer() -> str:
    return f"""<footer class="site-footer"><div class="wrap footer-inner">
    <div class="footer-links">
      <a href="/privacy">Privacy Policy</a>
      <a href="/terms">Terms of Service</a>
      <a href="/brand/third-party-notices.txt">Third-party notices</a>
      <a href="{SUPPORT_EMAIL_URL}">Support and contact</a>
      <a href="{SUPPORT_SERVER_URL}">Support server</a>
      <a href="/#listings">Bot listings</a>
      <a href="{PROJECT_URL}">Source code</a>
    </div>
    <p>Professor Compressor is not affiliated with Discord.</p>
  </div></footer>"""
