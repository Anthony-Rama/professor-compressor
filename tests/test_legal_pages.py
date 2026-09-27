import unittest

from professor_compressor.application import legal_response
from professor_compressor.legal_pages import privacy_policy_html, terms_of_service_html


class LegalPageTests(unittest.TestCase):
    def test_privacy_policy_describes_actual_data_flow(self) -> None:
        document = privacy_policy_html()

        self.assertIn("WebAssembly inside your browser", document)
        self.assertIn("file is uploaded unchanged", document)
        self.assertNotIn("are not uploaded to Professor Compressor", document)
        self.assertNotIn("open-source", document)
        self.assertIn("mailto:professorcompressor.support@gmail.com", document)
        self.assertIn("held temporarily in server\n        memory", document)
        self.assertIn("does not intentionally write video files to disk", document)
        self.assertIn("IP address", document)
        self.assertIn("does not sell personal information", document)
        self.assertIn("command user's Discord username and user ID", document)
        self.assertIn("No feedback is sent unless you choose to", document)
        self.assertIn('href="/terms"', document)

    def test_terms_cover_content_abuse_and_service_limits(self) -> None:
        document = terms_of_service_html()

        self.assertIn("own the content or have every permission", document)
        self.assertIn("Acceptable use", document)
        self.assertIn("rate, memory, concurrency, or availability limits", document)
        self.assertIn("not affiliated with or endorsed by Discord Inc.", document)
        self.assertIn("mailto:professorcompressor.support@gmail.com", document)
        self.assertIn('href="/privacy"', document)

    def test_legal_response_is_public_html(self) -> None:
        response = legal_response(privacy_policy_html())

        self.assertEqual(response.content_type, "text/html")
        self.assertEqual(response.headers["Cache-Control"], "public, max-age=3600")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()
