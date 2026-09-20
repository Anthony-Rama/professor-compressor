import unittest

from web_ui import browser_compressor


class BrowserPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page = browser_compressor(10, 19_000_000, "session_secret-123", 600)

    def test_replaces_private_page_values(self) -> None:
        self.assertIn('const MAX_CLIPS = 10;', self.page)
        self.assertIn('const TARGET_BYTES = 19000000;', self.page)
        self.assertIn('const SESSION_SECRET = "session_secret-123";', self.page)
        self.assertNotIn("__SESSION_SECRET__", self.page)

    def test_explains_local_and_remote_phases(self) -> None:
        self.assertIn("Compressing on this device", self.page)
        self.assertIn("Sending to Discord", self.page)
        self.assertIn("Only finished files are sent", self.page)

    def test_includes_retry_cancel_and_metrics(self) -> None:
        self.assertIn("Cancel", self.page)
        self.assertIn("Retrying automatically", self.page)
        self.assertIn("% smaller", self.page)
        self.assertIn("Estimating time remaining", self.page)

    def test_hides_encoder_implementation_loading_messages(self) -> None:
        self.assertNotIn("Loading the faster multithreaded compressor", self.page)
        self.assertNotIn("Loading the compatible compressor", self.page)


if __name__ == "__main__":
    unittest.main()
