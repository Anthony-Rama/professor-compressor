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
            "TOPGG_API_TOKEN": " topgg-secret ",
            "TOPGG_STATS_INTERVAL_MINUTES": "1",
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
        self.assertEqual(settings.topgg_api_token, "topgg-secret")
        self.assertEqual(settings.topgg_stats_interval_seconds, 1800)

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
            topgg_api_token="topgg-secret",
        )

        rendered = repr(settings)

        self.assertNotIn("discord-secret", rendered)
        self.assertNotIn("webhook-secret", rendered)
        self.assertNotIn("dsc-secret", rendered)
        self.assertNotIn("topgg-secret", rendered)

    def test_topgg_defaults_and_invalid_interval(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("professor_compressor.config.load_dotenv"),
        ):
            settings = Settings.from_environment(require_token=False)
            self.assertEqual(settings.topgg_api_token, "")
            self.assertEqual(settings.topgg_stats_interval_seconds, 3600)
            os.environ["TOPGG_STATS_INTERVAL_MINUTES"] = "bad"
            with self.assertRaisesRegex(RuntimeError, "TOPGG_STATS_INTERVAL"):
                Settings.from_environment(require_token=False)

    def test_operator_configuration_requires_strong_key_https_and_bounded_retention(
        self,
    ):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("professor_compressor.config.load_dotenv"),
        ):
            self.assertEqual(
                Settings.from_environment(require_token=False).observability_db_path, ""
            )
            os.environ["OBSERVABILITY_ADMIN_KEY"] = "short"
            with self.assertRaisesRegex(RuntimeError, "32 characters"):
                Settings.from_environment(require_token=False)
            os.environ["OBSERVABILITY_ADMIN_KEY"] = "test-only-" * 5
            os.environ["PUBLIC_BASE_URL"] = "http://public.example.com"
            with self.assertRaisesRegex(RuntimeError, "HTTPS"):
                Settings.from_environment(require_token=False)
            os.environ["PUBLIC_BASE_URL"] = "https://public.example.com"
            os.environ["OBSERVABILITY_RETENTION_DAYS"] = "31"
            with self.assertRaisesRegex(RuntimeError, "at most 30"):
                Settings.from_environment(require_token=False)
            os.environ["OBSERVABILITY_RETENTION_DAYS"] = "7"
            os.environ["ALLOWED_GUILD_ID"] = "123"
            settings = Settings.from_environment(require_token=False)
            self.assertEqual(settings.diagnostic_test_guild_ids, frozenset())
            self.assertNotIn(os.environ["OBSERVABILITY_ADMIN_KEY"], repr(settings))


if __name__ == "__main__":
    unittest.main()
