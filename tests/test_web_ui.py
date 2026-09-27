import asyncio
import shutil
import subprocess
import unittest
from unittest.mock import AsyncMock, Mock

from aiohttp import web

from professor_compressor.application import browser_security_headers, page
from professor_compressor.web_ui import browser_compressor


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
        self.assertIn("const MAX_CLIPS = 10;", self.page)
        self.assertIn("const TARGET_BYTES = 19000000;", self.page)
        self.assertIn("const MAX_BATCH_BYTES = 220000000;", self.page)
        self.assertIn('const SESSION_SECRET = "session_secret-123";', self.page)
        self.assertNotIn("__SESSION_SECRET__", self.page)

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

    def test_feedback_is_optional_and_has_a_support_link(self) -> None:
        self.assertIn(
            'id="feedback-actions" class="feedback-actions" hidden', self.page
        )
        self.assertIn("Share feedback", self.page)
        self.assertIn("Get support", self.page)
        self.assertIn("mailto:anthonyhrama@gmail.com", self.page)
        self.assertIn('showFeedback("success")', self.page)
        self.assertIn('showFeedback("failure")', self.page)

    def test_includes_retry_cancel_and_metrics(self) -> None:
        self.assertIn("Cancel", self.page)
        self.assertIn("Retrying automatically", self.page)
        self.assertIn("% smaller", self.page)
        self.assertIn("Video ", self.page)
        self.assertIn("Sending ", self.page)
        self.assertNotIn("Estimating time remaining", self.page)

    def test_reports_browser_failures_privately(self) -> None:
        self.assertIn('X-Upload-Session": SESSION_SECRET', self.page)
        self.assertIn('"/failure"', self.page)
        self.assertIn('runStage = "Browser compression"', self.page)
        self.assertIn('runStage = "Relay upload or Discord delivery"', self.page)
        self.assertIn("void reportBrowserFailure(runStage)", self.page)

    def test_targets_most_of_discords_safe_upload_size(self) -> None:
        self.assertIn("const OUTPUT_TARGET_RATIO = 0.97;", self.page)
        self.assertIn(
            "effectiveTargetBytes * 8 * OUTPUT_TARGET_RATIO",
            self.page,
        )

    def test_caps_each_output_to_the_batch_relay_limit(self) -> None:
        self.assertIn(
            "MAX_BATCH_BYTES / Math.max(1, selectedFiles.length)",
            self.page,
        )
        self.assertIn(
            "return Math.min(TARGET_BYTES, batchTargetBytes)",
            self.page,
        )
        self.assertIn("state.file.size > effectiveTargetBytes", self.page)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_generated_module_has_valid_javascript(self) -> None:
        script = self.page.split('<script type="module">', 1)[1].split("</script>", 1)[
            0
        ]
        subprocess.run(
            ["node", "--input-type=module", "--check"],
            input=script,
            text=True,
            capture_output=True,
            check=True,
        )

    def test_protects_active_compression_from_sleep_and_hidden_tabs(self) -> None:
        self.assertIn('navigator.wakeLock.request("screen")', self.page)
        self.assertIn('document.addEventListener("visibilitychange"', self.page)
        self.assertIn("Compression may slow down or pause", self.page)
        self.assertIn("Return to compressor", self.page)
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


if __name__ == "__main__":
    unittest.main()
