import unittest

from app import page
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
        self.assertIn("Video ", self.page)
        self.assertIn("Sending ", self.page)
        self.assertNotIn("Estimating time remaining", self.page)

    def test_targets_most_of_discords_safe_upload_size(self) -> None:
        self.assertIn("const OUTPUT_TARGET_RATIO = 0.97;", self.page)
        self.assertIn(
            "TARGET_BYTES * 8 * OUTPUT_TARGET_RATIO",
            self.page,
        )

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


if __name__ == "__main__":
    unittest.main()
