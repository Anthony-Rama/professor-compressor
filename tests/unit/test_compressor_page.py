import asyncio
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from aiohttp import web

from professor_compressor.web.pages.compressor import (
    LINK_UNAVAILABLE_BODY,
    MAINTENANCE_BODY,
    browser_compressor,
    browser_delivery_status,
    rate_limited_body,
)
from professor_compressor.web.pages.document import page
from professor_compressor.web.pages.landing import guide_html, home_html
from professor_compressor.web.pages.legal import (
    privacy_policy_html,
    terms_of_service_html,
)
from professor_compressor.web.security import browser_security_headers


def page_config(document: str, element_id: str) -> dict:
    marker = f'<script type="application/json" id="{element_id}">'
    return json.loads(document.split(marker, 1)[1].split("</script>", 1)[0])


class BrowserPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = browser_compressor(
            10,
            19_000_000,
            220_000_000,
            "session_secret-123",
            600,
        )

    def test_replaces_private_page_values(self) -> None:
        config = page_config(self.page, "compressor-config")
        self.assertEqual(config["MAX_CLIPS"], 10)
        self.assertEqual(config["TARGET_BYTES"], 19_000_000)
        self.assertEqual(config["DISCORD_LIMIT_BYTES"], 19_000_000)
        self.assertEqual(config["MAX_BATCH_BYTES"], 220_000_000)
        self.assertEqual(config["SESSION_SECRET"], "session_secret-123")
        self.assertNotIn("__CONFIG_JSON__", self.page)

    def test_delivery_page_serializes_private_configuration_safely(self):
        secret = "</script><script>not executable</script>"
        document = browser_delivery_status(secret, diagnostic_reference="PC-TEST")
        self.assertEqual(
            page_config(document, "delivery-config"),
            {"SESSION_SECRET": secret, "DIAGNOSTIC_REFERENCE": "PC-TEST"},
        )
        self.assertNotIn(secret, document)

    def test_escapes_session_secret_as_javascript_data(self) -> None:
        page = browser_compressor(
            1,
            100,
            100,
            '</script><script>alert("x")</script>',
            60,
        )

        self.assertIn(r"\u003c/script\u003e\u003cscript\u003ealert(\"x\")", page)
        self.assertNotIn('</script><script>alert("x")</script>', page)

    def test_explains_local_and_remote_phases(self) -> None:
        self.assertIn("Compressing on this device", self.page)
        self.assertIn("Sending to Discord", self.page)
        self.assertIn("Only finished files are sent", self.page)
        self.assertIn("MP4s that already fit may be sent unchanged", self.page)
        self.assertIn('href="/privacy"', self.page)
        self.assertIn('href="/terms"', self.page)

    def test_picker_has_clear_button_instead_of_replacement_hint(self) -> None:
        self.assertIn('id="clear-selection" type="button"', self.page)
        self.assertIn('aria-label="Clear selected videos"', self.page)
        self.assertNotIn("a new selection replaces the previous one", self.page)
        self.assertIn("Up to 10 clips · 1 GiB each · 4 GiB total", self.page)

    def test_feedback_is_optional_and_has_a_support_link(self) -> None:
        self.assertIn(
            'id="feedback-actions" class="feedback-actions contact-actions" hidden',
            self.page,
        )
        self.assertIn("Contact us</a>", self.page)
        self.assertIn("Join our server</a>", self.page)
        self.assertIn("https://discord.com/invite/32RWwNWyEH", self.page)
        self.assertIn("mailto:professorcompressor.support@gmail.com", self.page)

    def test_optional_caption_has_visible_limit(self) -> None:
        self.assertIn('id="caption" name="caption" maxlength="1000"', self.page)
        self.assertIn('class="caption-help">1,000-character limit.</p>', self.page)
        self.assertNotIn("Plain @names are text", self.page)

    def test_progress_and_cancel_are_in_reading_order(self):
        self.assertIn("Keep this page open until it finishes.", self.page)
        self.assertLess(self.page.index('id="work"'), self.page.index('id="cancel"'))

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_external_modules_have_valid_javascript(self) -> None:
        static = Path(__file__).resolve().parents[2] / "professor_compressor" / "static"
        for name in (
            "compressor.js",
            "delivery-status.js",
            "file-selection.js",
            "relay-client.js",
            "video-encoder.js",
        ):
            with self.subTest(asset=name):
                subprocess.run(
                    ["node", "--input-type=module", "--check"],
                    input=(static / name).read_text(encoding="utf-8"),
                    text=True,
                    capture_output=True,
                    check=True,
                )

    def test_explains_how_to_keep_compression_active(self) -> None:
        self.assertIn("chrome://settings/performance", self.page)
        self.assertIn("Always keep these", self.page)
        self.assertIn("Energy Saver", self.page)

    def test_hides_encoder_implementation_loading_messages(self) -> None:
        self.assertNotIn("Loading the faster multithreaded compressor", self.page)
        self.assertNotIn("Loading the compatible compressor", self.page)

    def test_large_batches_stay_inside_the_viewport(self) -> None:
        document = page("Test", self.page).text
        assert document is not None
        self.assertIn("align-items: flex-start", document)
        self.assertIn("margin-block: auto", document)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", document)
        self.assertIn(".file-card { width: 100%; min-width: 0", document)
        self.assertIn("flex: 1 1 auto; min-width: 0", document)

    def test_page_has_no_store_and_browser_security_headers(self) -> None:
        response = page("Test", self.page)

        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

        handler = AsyncMock(return_value=web.Response())
        secured = asyncio.run(browser_security_headers(Mock(), handler))
        policy = secured.headers["Content-Security-Policy"]
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)


class OutputSizeTargetingTests(unittest.TestCase):
    """Size targeting lives in the browser modules; these pin its rules."""

    STATIC = Path(__file__).resolve().parents[2] / "professor_compressor" / "static"

    def setUp(self) -> None:
        self.encoder = (self.STATIC / "video-encoder.js").read_text()
        self.controller = (self.STATIC / "batch-controller.js").read_text()

    def test_targets_most_of_discords_safe_upload_size(self) -> None:
        self.assertIn("const OUTPUT_TARGET_RATIO = 0.97;", self.encoder)
        self.assertIn("effectiveTargetBytes * 8 * OUTPUT_TARGET_RATIO", self.encoder)

    def test_caps_each_output_to_the_batch_relay_limit(self) -> None:
        # Each file gets an equal share of the relay's combined batch limit.
        self.assertEqual(
            self.encoder.count("MAX_BATCH_BYTES / Math.max(1, getFiles().length)"), 2
        )
        self.assertIn("return Math.min(TARGET_BYTES, batchTargetBytes)", self.encoder)
        self.assertIn(
            "return Math.min(DISCORD_LIMIT_BYTES, batchTargetBytes)", self.encoder
        )
        self.assertIn(
            "state.file.size > encoder.unchangedLimitBytes()", self.controller
        )
        self.assertIn("file.size <= unchangedLimitBytes()", self.encoder)


class ThemeChoiceMarkupTests(unittest.TestCase):
    """Every page applies a saved theme before paint and offers the toggle."""

    def documents(self) -> dict[str, str]:
        session_bodies = {
            "compressor": browser_compressor(10, 1, 1, "secret", 60),
            "delivery status": browser_delivery_status("secret"),
            "unavailable": LINK_UNAVAILABLE_BODY,
            "maintenance": MAINTENANCE_BODY,
            "rate limited": rate_limited_body(5),
        }
        documents = {
            name: page("Test", body).text or "" for name, body in session_bodies.items()
        }
        documents.update(
            home=home_html("https://example.test"),
            guide=guide_html("https://example.test"),
            privacy=privacy_policy_html(),
            terms=terms_of_service_html(),
        )
        return documents

    def test_saved_theme_applies_first_and_controls_load_separately(self) -> None:
        for name, document in self.documents().items():
            with self.subTest(page=name):
                head = document.split("<head>", 1)[1].lstrip()
                self.assertTrue(
                    head.startswith(
                        '<meta charset="utf-8">\n  <script>try{var t=localStorage'
                        '.getItem("pc-theme");'
                    )
                )
                self.assertIn(
                    'import("/brand/theme-toggle.js").catch(() => {});', document
                )
                self.assertNotIn("__THEME", document)

    def test_session_pages_offer_a_theme_radio_group_outside_forms(self) -> None:
        documents = self.documents()
        for name in (
            "delivery status",
            "unavailable",
            "maintenance",
            "rate limited",
        ):
            with self.subTest(page=name):
                body = documents[name].split("<body>", 1)[1]
                header, _, rest = body.partition("</header>")
                switch = '<fieldset class="theme-switch" hidden>'
                self.assertNotIn(switch, header)
                footer = rest.split('<footer class="page-footer', 1)[1]
                container = footer.split("</footer>", 1)[0]
                self.assertIn(switch, container)
                self.assertIn(
                    '<legend class="visually-hidden">Theme</legend>', container
                )
                for value in ("system", "light", "dark"):
                    self.assertIn(
                        f'type="radio" name="theme-choice" value="{value}"', container
                    )
                self.assertNotIn("theme-menu", body)

    def test_compressor_and_website_offer_a_theme_menu_in_the_header(self) -> None:
        documents = self.documents()
        for name in ("compressor", "home", "guide", "privacy", "terms"):
            with self.subTest(page=name):
                body = documents[name].split("<body>", 1)[1]
                header = body.split("</header>", 1)[0]
                self.assertIn('aria-haspopup="menu" aria-expanded="false"', header)
                self.assertIn('aria-label="Theme: System"', header)
                self.assertEqual(header.count('role="menuitemradio"'), 3)
                self.assertNotIn("theme-switch", body)
