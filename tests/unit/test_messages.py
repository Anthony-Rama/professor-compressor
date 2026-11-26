import json
from unittest.mock import Mock

from professor_compressor.bot.messages import (
    botstats_report,
    compression_outcome_alert,
    guild_alert_message,
    safe_alert_text,
)
from professor_compressor.domain import (
    BrowserResult,
    CompressionDiagnostic,
    UploadJob,
)
from professor_compressor.relay.diagnostics import (
    parse_compression_diagnostics,
)
from tests.support.runtime import RuntimeTestCase


class OperatorMessageTests(RuntimeTestCase):
    def test_full_batch_telemetry_alert_is_bounded(self):
        job = UploadJob(
            "x",
            123,
            456,
            9999999999,
            20 * 1024 * 1024,
            Mock(),
            guild_name="g" * 100,
            username="u" * 100,
        )
        telemetry = {
            "engine": "multithreaded",
            "engine_seconds": 86400,
            "hidden_seconds": 86400,
            "pre_upload_seconds": 86400,
            "upload_attempts": 99,
        }
        message = compression_outcome_alert(
            job,
            succeeded=True,
            file_count=10,
            results=[BrowserResult("private.mp4", b"x")] * 10,
            diagnostics=[
                CompressionDiagnostic(
                    10**12, 86400, 10**9, False, "asf/wmv", 86400, telemetry
                )
            ]
            * 10,
            effective_target=19 * 1024 * 1024,
            unchanged_limit=20 * 1024 * 1024,
            timing={
                "relay_receive_seconds": 300,
                "queue_seconds": 40,
                "discord_delivery_seconds": 120,
            },
        )
        self.assertLessEqual(len(message), 2000)
        self.assertIn("Compression time (encoding only)", message)
        self.assertIn("Receive / queue / Discord", message)
        self.assertNotIn("private.mp4", message)

    def test_encoding_time_is_optional_bounded_and_not_clip_duration(self):
        base = {
            "input_bytes": 10,
            "duration_seconds": 74.3,
            "video_kbps": 100,
            "copied": False,
        }
        for value, expected in (
            (0, 0),
            (18.125, 18.125),
            (86400, 86400),
            (-1, None),
            (86401, None),
            (10**1000, None),
            (True, None),
            ("18", None),
            (float("nan"), None),
            (float("inf"), None),
        ):
            with self.subTest(value=value):
                diagnostic = parse_compression_diagnostics(
                    json.dumps([{**base, "encoding_seconds": value}]).encode(),
                    max_clips=10,
                )[0]
                self.assertEqual(diagnostic.encoding_seconds, expected)
                self.assertEqual(diagnostic.duration_seconds, 74.3)
        self.assertIsNone(
            parse_compression_diagnostics(json.dumps([base]).encode(), max_clips=10)[
                0
            ].encoding_seconds
        )

    def test_alert_sums_only_complete_encoder_timings(self):
        job = UploadJob("x", 123, 456, 9999999999, 20 * 1024 * 1024, Mock())
        results = [BrowserResult("private.mp4", b"x")] * 2

        def alert(diagnostics):
            return compression_outcome_alert(
                job,
                succeeded=True,
                file_count=2,
                results=results,
                diagnostics=diagnostics,
                effective_target=19 * 1024 * 1024,
                unchanged_limit=20 * 1024 * 1024,
                elapsed_seconds=2039,
            )

        first = CompressionDiagnostic(100, 74.3, 100, False, "mov", 19.95)
        second = CompressionDiagnostic(100, None, None, True, "mp4", 0)
        message = alert([first, second])
        self.assertIn("Compression time (encoding only): `0:20.0`", message)
        self.assertIn("encode `0:20.0`", message)
        self.assertIn("74.3s clip", message)
        self.assertIn("Elapsed: `33:59`", message)
        self.assertIn(
            "Compression time (encoding only): `0:39.9`", alert([first, first])
        )
        old = CompressionDiagnostic(100, None, None, True, "mp4")
        self.assertNotIn("Compression time (encoding only)", alert([first, old]))
        self.assertIn(
            "Compression time (encoding only): `0:00.0`", alert([second, second])
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

        self.assertEqual(
            self.runtime.commands.interaction_guild_name(interaction), "Unknown server"
        )

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
            CompressionDiagnostic(10**12, 86400.0, 10**9, False, "asf/wmv", 86400)
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
