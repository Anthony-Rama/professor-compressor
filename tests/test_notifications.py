import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from professor_compressor.application import (
    JobState,
    UploadJob,
    botstats_report,
    compression_outcome_alert,
    guild_alert_message,
    interaction_guild_name,
    jobs,
    on_guild_join,
    receive_browser_failure,
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

    def test_partial_guild_uses_unknown_server_fallback(self) -> None:
        interaction = Mock()
        interaction.guild.name = ""
        interaction.guild_id = 123

        self.assertEqual(interaction_guild_name(interaction), "Unknown server")

    def test_success_alert_contains_delivery_metrics_without_filenames(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            guild_id=789,
            guild_name="Test Server",
        )

        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=2,
            total_bytes=5 * 1024 * 1024,
            elapsed_seconds=65,
        )

        self.assertIn("delivered successfully", message)
        self.assertIn("Test Server", message)
        self.assertIn("User ID: `123`", message)
        self.assertNotIn("<@123>", message)
        self.assertIn("Files: `2`", message)
        self.assertIn("Finished size: `5.0 MiB`", message)
        self.assertIn("Elapsed: `1:05`", message)
        self.assertNotIn("clip.mp4", message)

    async def test_browser_failure_is_authenticated_and_allows_retry(self) -> None:
        job = UploadJob(
            token="failure-token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
            guild_id=789,
            guild_name="Test Server",
        )
        jobs[job.token] = job
        request = Mock()
        request.match_info = {"token": job.token}
        request.headers = {"X-Upload-Session": "claim-secret"}
        request.json = AsyncMock(return_value={"stage": "Browser compression<script>"})
        request.clone.return_value = request
        try:
            with patch(
                "professor_compressor.application.schedule_owner_alert"
            ) as schedule_alert:
                response = await receive_browser_failure(request)

            self.assertEqual(response.status, 200)
            self.assertIs(job.state, JobState.CLAIMED)
            self.assertIn(job.token, jobs)
            self.assertTrue(job.browser_failure_reported)
            alert = schedule_alert.call_args.args[0]
            self.assertIn("Compression failed", alert)
            self.assertIn("User ID: `123`", alert)
            self.assertIn("Browser compressionscript", alert)
            self.assertNotIn("<script>", alert)
        finally:
            jobs.pop(job.token, None)

    async def test_browser_failure_alert_is_only_sent_once(self) -> None:
        job = UploadJob(
            token="failure-token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
            browser_failure_reported=True,
        )
        jobs[job.token] = job
        request = Mock()
        request.match_info = {"token": job.token}
        request.headers = {"X-Upload-Session": "claim-secret"}
        request.json = AsyncMock(return_value={"stage": "Browser compression"})
        request.clone.return_value = request
        try:
            with patch(
                "professor_compressor.application.schedule_owner_alert"
            ) as schedule_alert:
                response = await receive_browser_failure(request)
        finally:
            jobs.pop(job.token, None)

        self.assertEqual(response.status, 200)
        schedule_alert.assert_not_called()

    async def test_join_event_schedules_notification(self) -> None:
        guild = Mock(name="guild")
        guild.name = "Test Server"
        guild.id = 123
        guild.member_count = 42

        with (
            patch(
                "professor_compressor.application.schedule_owner_alert"
            ) as schedule_alert,
            patch(
                "professor_compressor.application.schedule_dsc_stats_update"
            ) as schedule_stats,
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
            patch("professor_compressor.application.client", mock_client),
            patch("professor_compressor.application.DSC_API_TOKEN", "test-api-token"),
            patch(
                "professor_compressor.application.ClientSession",
                return_value=session_context,
            ),
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
