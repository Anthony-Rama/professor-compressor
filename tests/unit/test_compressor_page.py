import asyncio
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from aiohttp import web

from professor_compressor.web.pages.compressor import (
    browser_compressor,
    browser_delivery_status,
)
from professor_compressor.web.pages.document import page
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
        self.assertIn("Add videos in batches, up to 10 total", self.page)

    def test_feedback_is_optional_and_has_a_support_link(self) -> None:
        self.assertIn(
            'id="feedback-actions" class="feedback-actions contact-actions" hidden',
            self.page,
        )
        self.assertIn("Contact Us", self.page)
        self.assertIn("Join Discord Server", self.page)
        self.assertIn("https://discord.com/invite/32RWwNWyEH", self.page)
        self.assertIn("mailto:professorcompressor.support@gmail.com", self.page)

    def test_optional_caption_has_visible_limit(self) -> None:
        self.assertIn('id="caption" name="caption" maxlength="1000"', self.page)
        self.assertIn('class="caption-help">1,000-character limit.</p>', self.page)
        self.assertNotIn("Plain @names are text", self.page)

    def test_progress_and_cancel_are_in_reading_order(self):
        self.assertIn("Keep this page open and active until it finishes.", self.page)
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
