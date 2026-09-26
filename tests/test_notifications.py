import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from app import (
    botstats_report,
    guild_alert_message,
    on_guild_join,
    report_dsc_stats,
    safe_alert_text,
)


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_safe_alert_text_escapes_markdown_and_backticks(self) -> None:
        self.assertEqual(safe_alert_text("**name** `ping`"), "\\*\\*name\\*\\* 'ping'")

    def test_guild_alert_contains_operational_data_only(self) -> None:
        guild = Mock(name="guild")
        guild.name = "Test Server"
        guild.id = 123
        guild.member_count = 42

        message = guild_alert_message("🟢 Installed", guild, server_count=2)

        self.assertIn("Test Server", message)
        self.assertIn("`123`", message)
        self.assertIn("`42`", message)
        self.assertIn("Connected servers: `2`", message)

    async def test_join_event_schedules_notification(self) -> None:
        guild = Mock(name="guild")
        guild.name = "Test Server"
        guild.id = 123
        guild.member_count = 42

        with (
            patch("app.schedule_owner_alert") as schedule_alert,
            patch("app.schedule_dsc_stats_update") as schedule_stats,
        ):
            await on_guild_join(guild)

        schedule_alert.assert_called_once()
        schedule_stats.assert_called_once_with()
        self.assertIn("installed", schedule_alert.call_args.args[0])

    async def test_dsc_stats_report_uses_current_server_count(self) -> None:
        mock_client = Mock()
        mock_client.user.id = 1550752400271351839
        mock_client.guilds = [Mock(), Mock(), Mock()]

        response = AsyncMock()
        response.raise_for_status = Mock()
        response_context = AsyncMock()
        response_context.__aenter__.return_value = response
        session = Mock()
        session.post.return_value = response_context
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session

        with (
            patch("app.client", mock_client),
            patch("app.DSC_API_TOKEN", "test-api-token"),
            patch("app.ClientSession", return_value=session_context),
        ):
            self.assertTrue(await report_dsc_stats())

        session.post.assert_called_once()
        request = session.post.call_args
        self.assertEqual(
            request.args[0],
            "https://dsc.sh/api/bots/1550752400271351839/stats",
        )
        self.assertEqual(request.kwargs["json"], {"server_count": 3})
        self.assertEqual(request.kwargs["headers"]["Authorization"], "test-api-token")
        response.raise_for_status.assert_called_once_with()

    def test_botstats_report_lists_servers_and_aggregate_usage(self) -> None:
        first_guild = Mock(name="first_guild")
        first_guild.name = "Zulu Server"
        first_guild.id = 456
        first_guild.member_count = 12
        second_guild = Mock(name="second_guild")
        second_guild.name = "Alpha Server"
        second_guild.id = 123
        second_guild.member_count = 34
        snapshot = {
            "sessions_created": 5,
            "deliveries_succeeded": 4,
        }

        report = botstats_report([first_guild, second_guild], snapshot)

        self.assertIn("Connected servers: **2**", report)
        self.assertIn("Sessions since restart: **5**", report)
        self.assertLess(report.index("Alpha Server"), report.index("Zulu Server"))


if __name__ == "__main__":
    unittest.main()
