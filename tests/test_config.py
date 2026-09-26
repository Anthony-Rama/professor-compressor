import os
import unittest
from unittest.mock import patch

from professor_compressor.config import MIB, Settings


class SettingsTests(unittest.TestCase):
    def test_loads_and_validates_environment(self) -> None:
        environment = {
            "DISCORD_TOKEN": "private-token",
            "WEB_PORT": "9000",
            "PUBLIC_BASE_URL": "https://compressor.example.com/",
            "ALLOWED_GUILD_IDS": "123, 456",
            "MAX_RESULT_TOTAL_MIB": "50",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("professor_compressor.config.load_dotenv"),
        ):
            settings = Settings.from_environment()

        self.assertEqual(settings.web_port, 9000)
        self.assertEqual(settings.public_base_url, "https://compressor.example.com")
        self.assertEqual(settings.allowed_guild_ids, frozenset({123, 456}))
        self.assertEqual(settings.max_result_total_bytes, 50 * MIB)

    def test_requires_token_at_runtime(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("professor_compressor.config.load_dotenv"),
            self.assertRaisesRegex(RuntimeError, "DISCORD_TOKEN"),
        ):
            Settings.from_environment()

    def test_rejects_invalid_public_url(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"DISCORD_TOKEN": "token", "PUBLIC_BASE_URL": "not-a-url"},
                clear=True,
            ),
            patch("professor_compressor.config.load_dotenv"),
            self.assertRaisesRegex(RuntimeError, "PUBLIC_BASE_URL"),
        ):
            Settings.from_environment()

    def test_secret_values_are_not_in_repr(self) -> None:
        settings = Settings(
            discord_token="discord-secret",
            alert_webhook_url="webhook-secret",
            dsc_api_token="dsc-secret",
        )

        rendered = repr(settings)

        self.assertNotIn("discord-secret", rendered)
        self.assertNotIn("webhook-secret", rendered)
        self.assertNotIn("dsc-secret", rendered)


if __name__ == "__main__":
    unittest.main()
