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
    parse_compression_diagnostics,
    receive_browser_failure,
    report_dsc_stats,
    safe_alert_text,
)
from professor_compressor.domain import BrowserResult, CompressionDiagnostic


class NotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_invalid_browser_diagnostics_are_ignored(self) -> None:
        self.assertEqual(parse_compression_diagnostics(b"not json"), [])
        self.assertEqual(parse_compression_diagnostics(b"[]"), [])
        self.assertEqual(parse_compression_diagnostics(b'[{"copied":true}]'), [])
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":NaN,"video_kbps":100,"copied":false}]'
            ),
            [],
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":1.5,"video_kbps":null,"copied":false}]'
            )[0].video_kbps,
            None,
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":null,"video_kbps":null,"copied":true,"input_format":"mov"}]'
            )[0].input_format,
            "mov",
        )
        self.assertEqual(
            parse_compression_diagnostics(
                b'[{"input_bytes":10,"duration_seconds":null,"video_kbps":null,"copied":true,"input_format":"secret.mp4"}]'
            ),
            [],
        )

    def test_safe_alert_text_escapes_markdown_and_backticks(self) -> None:
        self.assertEqual(safe_alert_text("**name** `ping`"), "\\*\\*name\\*\\* 'ping'")
        self.assertIn("@\u200beveryone", safe_alert_text("@everyone"))

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
            username="clip_creator",
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
        self.assertIn("Username: **@clip\\_creator**", message)
        self.assertIn("User ID: `123`", message)
        self.assertNotIn("<@123>", message)
        self.assertIn("Files: `2`", message)
        self.assertIn("Finished size: `5.0 MiB`", message)
        self.assertIn("Elapsed: `1:05`", message)
        self.assertNotIn("clip.mp4", message)

    def test_single_file_diagnostics_show_measured_output_and_target(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
        )
        result = BrowserResult("private-clip.mp4", b"x" * 1024)
        diagnostic = CompressionDiagnostic(84 * 1024 * 1024, 74.3, 2010, False, "mov")
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=1,
            total_bytes=1024,
            results=[result],
            diagnostics=[diagnostic],
            effective_target=int(19.6 * 1024 * 1024),
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertIn("Input size: `84.0 MiB`", message)
        self.assertIn("Format: `MOV → MP4`", message)
        self.assertNotIn("Finished size:", message)
        self.assertIn("Discord limit: `20.0 MiB`", message)
        self.assertIn("Effective target per file: `19.6 MiB`", message)
        self.assertIn("Detected duration: `74.3 sec`", message)
        self.assertIn("Calculated video bitrate: `2010 kbps`", message)
        self.assertIn("Actual output: `0.0 MiB`", message)
        self.assertNotIn("private-clip", message)

    def test_size_reduction_uses_original_and_server_measured_output(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
        )
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=1,
            results=[BrowserResult("secret.mp4", b"x" * 1024)],
            diagnostics=[CompressionDiagnostic(2048, 5.0, 1000, False, "mov")],
            effective_target=19 * 1024 * 1024,
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertIn("Format: `MOV → MP4`", message)
        self.assertIn("Size reduction: `50.0%`", message)
        self.assertNotIn("secret.mp4", message)

    def test_batch_diagnostics_fit_discord_alert_limit(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
            guild_name="G" * 100,
            username="u" * 100,
        )
        results = [BrowserResult(f"private-{i}.mp4", b"x") for i in range(10)]
        diagnostics = [
            CompressionDiagnostic(1000, None, None, True, "asf/wmv") for _ in results
        ]
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=10,
            results=results,
            diagnostics=diagnostics,
            effective_target=19 * 1024 * 1024,
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertLessEqual(len(message), 2000)
        self.assertIn("File 10:", message)
        self.assertIn("already fit", message)
        self.assertIn("ASF/WMV→MP4", message)
        self.assertIn("99.9% smaller", message)
        self.assertNotIn("private-", message)

    def test_10_encoded_file_diagnostics_fit_discord_alert_limit(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
            guild_name="G" * 100,
            username="u" * 100,
        )
        results = [BrowserResult(f"private-{i}.mp4", b"x" * 1024) for i in range(10)]
        diagnostics = [
            CompressionDiagnostic(10**12, 86400.0, 10**9, False, "asf/wmv")
            for _ in results
        ]
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=10,
            results=results,
            diagnostics=diagnostics,
            effective_target=19 * 1024 * 1024,
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertLessEqual(len(message), 2000)
        self.assertIn("File 10:", message)
        self.assertNotIn("private-", message)

    def test_fitting_mp4_uses_discord_limit_in_utilization(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
        )
        result = BrowserResult("private.mp4", b"x" * 1024)
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=1,
            results=[result],
            diagnostics=[CompressionDiagnostic(1024, None, None, True)],
            effective_target=int(19.6 * 1024 * 1024),
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertIn("Effective target per file: `20.0 MiB`", message)
        self.assertIn("Encoding: `skipped (MP4 already fit)`", message)

    def test_quality_conversion_has_no_claimed_target_bitrate(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
        )
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=1,
            results=[BrowserResult("private.mp4", b"x" * 1024)],
            diagnostics=[CompressionDiagnostic(2048, 5.0, None, False)],
            effective_target=int(19.6 * 1024 * 1024),
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertIn("Effective target per file: `20.0 MiB`", message)
        self.assertIn("quality-based MP4 conversion", message)
        self.assertNotIn("Calculated video bitrate", message)

    def test_stream_copy_is_distinguished_from_unchanged_mp4(self) -> None:
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20 * 1024 * 1024,
            interaction=Mock(),
        )
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=1,
            results=[BrowserResult("private.mp4", b"x" * 1024)],
            diagnostics=[CompressionDiagnostic(2048, 5.0, None, True)],
            effective_target=int(19.6 * 1024 * 1024),
            unchanged_limit=20 * 1024 * 1024,
            elapsed_seconds=1,
        )
        self.assertIn("stream-copied to MP4", message)

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
            username="clip_creator",
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
            self.assertIn("Username: **@clip\\_creator**", alert)
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
