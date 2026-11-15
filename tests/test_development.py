import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from professor_compressor import development as dev


class DevelopmentLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "dev.env"
        self.values = {
            "DISCORD_TOKEN": "private-dev-token",
            "DEV_APPLICATION_ID": "987654321",
            "ALLOWED_GUILD_IDS": "123,456",
            "PUBLIC_BASE_URL": "https://dev.example.com",
        }

    def write(self, **updates: str) -> Path:
        values = self.values | updates
        self.path.write_text(
            "\n".join(f"{key}={value}" for key, value in values.items())
        )
        self.path.chmod(0o600)
        return self.path

    def test_development_environment_excludes_inherited_secrets_and_integration_ids(
        self,
    ):
        inherited = {
            "PATH": "/safe/path",
            "DISCORD_TOKEN": "production-token",
            "ALLOWED_GUILD_ID": "999",
            "TOPGG_API_TOKEN": "production-topgg",
            "ALERT_WEBHOOK_URL": "production-webhook",
            "BOTSTATS_GUILD_ID": "999",
            "OBSERVABILITY_DB_PATH": "/production/events.sqlite3",
            "OBSERVABILITY_ADMIN_KEY": "production-admin-key",
            "PYTHONPATH": "/untrusted/python",
        }
        with patch.dict(os.environ, inherited, clear=True):
            environment = dev.development_environment(self.write())
        self.assertEqual(environment["DISCORD_TOKEN"], "private-dev-token")
        self.assertEqual(environment["ALLOWED_GUILD_IDS"], "123,456")
        self.assertEqual(environment["DIAGNOSTIC_TEST_GUILD_IDS"], "123,456")
        self.assertEqual(environment["PYTHON_DOTENV_DISABLED"], "1")
        self.assertEqual(environment["TOPGG_API_TOKEN"], "")
        self.assertEqual(environment["ALERT_WEBHOOK_URL"], "")
        self.assertEqual(environment["OBSERVABILITY_ADMIN_KEY"], "")
        self.assertNotIn("BOTSTATS_GUILD_ID", environment)
        self.assertNotIn("PYTHONPATH", environment)
        self.assertTrue(
            environment["OBSERVABILITY_DB_PATH"].endswith("/dev/events.sqlite3")
        )

    def test_cannot_use_production_environment_file(self):
        with self.assertRaisesRegex(dev.DevelopmentError, "production .env"):
            dev.development_environment(dev.ROOT / ".env")

    def test_missing_file_does_not_fall_back_to_dotenv(self):
        with self.assertRaisesRegex(dev.DevelopmentError, "not found"):
            dev.development_environment(self.path)

    @unittest.skipUnless(os.name == "posix", "POSIX permissions")
    def test_credentials_must_not_be_readable_by_others(self):
        self.write().chmod(0o644)
        with self.assertRaisesRegex(dev.DevelopmentError, "owner-only"):
            dev.development_environment(self.path)

    def test_rejects_unsafe_or_incomplete_configuration(self):
        for updates in (
            {"DEV_APPLICATION_ID": ""},
            {"DEV_APPLICATION_ID": dev.PRODUCTION_APPLICATION_ID},
            {"DISCORD_TOKEN": ""},
            {"DISCORD_TOKEN": "token with spaces"},
            {"ALLOWED_GUILD_IDS": ""},
            {"ALLOWED_GUILD_IDS": "123,"},
            {"ALLOWED_GUILD_IDS": "-1"},
            {"ALLOWED_GUILD_IDS": str(2**64)},
            {"WEB_HOST": "0.0.0.0"},
            {"WEB_PORT": "bad"},
            {"WEB_PORT": "65536"},
            {"PUBLIC_BASE_URL": "https://" + dev.PRODUCTION_HOST},
            {"PUBLIC_BASE_URL": "https://" + dev.PRODUCTION_HOST.upper() + "."},
            {"PUBLIC_BASE_URL": "http://dev.example.com"},
            {"PUBLIC_BASE_URL": "https://user:secret@dev.example.com"},
            {"PUBLIC_BASE_URL": "https://dev.example.com/upload/private"},
            {"PUBLIC_BASE_URL": "https://dev.example.com?secret=test"},
            {"PUBLIC_BASE_URL": "http://127.0.0.1:9999"},
            {"PUBLIC_BASE_URL": "https://dev.example.com:bad"},
            {"TOPGG_API_TOKEN": "production-secret"},
            {"DSC_API_TOKEN": "production-secret"},
            {"ALERT_WEBHOOK_URL": "production-secret"},
            {"OBSERVABILITY_ADMIN_KEY": "short"},
            {"TYPO": "value"},
        ):
            with (
                self.subTest(fields=list(updates)),
                self.assertRaises(dev.DevelopmentError),
            ):
                dev.development_environment(self.write(**updates))

    def test_accepts_loopback_and_does_not_expand_inherited_secrets(self):
        with patch.dict(os.environ, {"INHERITED_SECRET": "must-not-expand"}):
            environment = dev.development_environment(
                self.write(
                    PUBLIC_BASE_URL="http://127.0.0.1:8080/",
                    DISCORD_TOKEN="${INHERITED_SECRET}",
                )
            )
        self.assertEqual(environment["DISCORD_TOKEN"], "${INHERITED_SECRET}")
        self.assertEqual(environment["PUBLIC_BASE_URL"], "http://127.0.0.1:8080")

    def test_check_verifies_identity_without_starting_application(self):
        path = self.write()
        with (
            patch("sys.argv", ["dev_bot.py", "--env-file", str(path), "--check"]),
            patch.object(dev, "verify_identity", AsyncMock()) as verify,
            patch.object(dev.subprocess, "run") as run,
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(dev.main(), 0)
        verify.assert_awaited_once_with("private-dev-token", "987654321")
        run.assert_not_called()
        self.assertNotIn("private-dev-token", output.getvalue())

    def test_identity_failure_cannot_start_child_or_print_secrets(self):
        path = self.write()
        with (
            patch("sys.argv", ["dev_bot.py", "--env-file", str(path)]),
            patch.object(
                dev,
                "verify_identity",
                AsyncMock(side_effect=OSError("private-dev-token")),
            ),
            patch.object(dev.subprocess, "run") as run,
            contextlib.redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(dev.main(), 1)
        run.assert_not_called()
        self.assertNotIn("private-dev-token", output.getvalue())

    def test_launcher_passes_only_isolated_environment_to_child(self):
        path = self.write()
        with (
            patch("sys.argv", ["dev_bot.py", "--env-file", str(path)]),
            patch.object(dev, "verify_identity", AsyncMock()),
            patch.object(dev.subprocess, "run", return_value=Mock(returncode=0)) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(dev.main(), 0)
        self.assertEqual(
            run.call_args.kwargs["env"]["DISCORD_TOKEN"], "private-dev-token"
        )
        self.assertEqual(run.call_args.kwargs["cwd"], dev.ROOT)


class DevelopmentIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_check_rejects_mismatch_production_and_http_errors(self):
        for status, identity in (
            (200, {"id": "987", "bot": True}),
            (200, {"id": "wrong", "bot": True}),
            (200, {"id": dev.PRODUCTION_APPLICATION_ID, "bot": True}),
            (200, {"id": "987", "bot": False}),
            (200, []),
            (401, {}),
            (429, {}),
            (302, {}),
        ):
            with self.subTest(status=status, identity=identity):
                response = AsyncMock()
                response.status = status
                response.content_length = 100
                response.json.return_value = identity
                with patch.object(dev, "ClientSession") as factory:
                    session = factory.return_value.__aenter__.return_value
                    session.get = Mock()
                    session.get.return_value.__aenter__ = AsyncMock(
                        return_value=response
                    )
                    session.get.return_value.__aexit__ = AsyncMock(return_value=False)
                    if status == 200 and identity == {"id": "987", "bot": True}:
                        await dev.verify_identity("secret", "987")
                    else:
                        with self.assertRaises(dev.DevelopmentError):
                            await dev.verify_identity("secret", "987")
                    self.assertFalse(session.get.call_args.kwargs["allow_redirects"])
