import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from professor_compressor.application import (
    BrowserResult,
    UploadJob,
    compression_target,
    deliver_browser_results,
    safe_result_name,
)
from professor_compressor.delivery_policy import (
    UNCONFIRMED_DELIVERY_MESSAGE,
    DeliveryFailure,
    delivery_failure,
)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_releases_memory_after_success_and_failure(self) -> None:
        from professor_compressor import application as app
        from professor_compressor.domain import DeliveryRequest, JobState

        for failure in (
            None,
            RuntimeError("Discord unavailable"),
            TimeoutError(),
            DeliveryFailure(
                "Ask an admin to restore access.", "DELIVERY_ACCESS_DENIED"
            ),
        ):
            job = UploadJob(
                token="worker-test",
                user_id=1,
                channel_id=2,
                expires_at=9999999999,
                discord_limit=1000,
                interaction=Mock(),
            )
            queue = asyncio.Queue(1)
            completed = asyncio.get_running_loop().create_future()
            queue.put_nowait(
                DeliveryRequest(job, [BrowserResult("clip.mp4", b"video")], completed)
            )
            with (
                patch.object(app, "delivery_queue", queue),
                patch.object(app, "delivery_bytes_held", 5),
                patch.object(app, "schedule_owner_alert"),
                patch.object(app, "outcomes", {}),
                patch.object(
                    app,
                    "deliver_browser_results",
                    AsyncMock(return_value="sent", side_effect=failure),
                ),
            ):
                worker = asyncio.create_task(app.delivery_worker(1))
                try:
                    await asyncio.wait_for(queue.join(), 2)
                    self.assertEqual(app.delivery_bytes_held, 0)
                    self.assertIs(job.state, JobState.DONE)
                    if failure is not None:
                        expected = delivery_failure(failure)
                        self.assertEqual(str(completed.exception()), str(expected))
                        receipt = app.outcomes[job.token].body
                        self.assertEqual(receipt["delivery_outcome"], expected.outcome)
                        self.assertEqual(receipt["error_code"], expected.error_code)
                    else:
                        self.assertEqual(completed.result(), "sent")
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

    def test_compression_target_keeps_small_delivery_reserve(self) -> None:
        self.assertEqual(compression_target(20_000_000), 19_600_000)

    def test_unknown_failures_are_not_rejections_or_raw_exception_messages(
        self,
    ) -> None:
        for error in (
            RuntimeError("private upstream detail"),
            OSError("secret"),
            TimeoutError(),
        ):
            failure = delivery_failure(error)
            self.assertEqual(str(failure), UNCONFIRMED_DELIVERY_MESSAGE)
            self.assertEqual(failure.outcome, "unknown")
            self.assertEqual(
                failure.error_code,
                "DELIVERY_TIMEOUT"
                if isinstance(error, TimeoutError)
                else "DELIVERY_UNCONFIRMED",
            )
        known = DeliveryFailure(
            "Ask an admin to restore access.", "DELIVERY_ACCESS_DENIED"
        )
        self.assertIs(delivery_failure(known), known)

    async def test_missing_destination_is_a_confirmed_access_failure(self) -> None:
        job = UploadJob(
            token="missing",
            user_id=1,
            channel_id=2,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
        )
        with patch(
            "professor_compressor.application.get_channel", AsyncMock(return_value=None)
        ):
            with self.assertRaises(DeliveryFailure) as caught:
                await deliver_browser_results(
                    job, [BrowserResult("clip.mp4", b"video")]
                )
        self.assertEqual(caught.exception.error_code, "DELIVERY_ACCESS_DENIED")
        self.assertEqual(caught.exception.outcome, "rejected")

    async def test_send_failures_preserve_certainty_and_safe_messages(self) -> None:
        import discord

        cases = (
            (OSError("private network detail"), "DELIVERY_UNCONFIRMED", "unknown"),
            (TimeoutError(), "DELIVERY_TIMEOUT", "unknown"),
            (
                discord.Forbidden(Mock(status=403, reason="Forbidden"), "denied"),
                "DELIVERY_ACCESS_DENIED",
                "rejected",
            ),
            (
                discord.HTTPException(
                    Mock(status=500, reason="Failure"), "upstream detail"
                ),
                "DELIVERY_UNCONFIRMED",
                "unknown",
            ),
            (
                discord.HTTPException(
                    Mock(status=400, reason="Bad request"), "upstream detail"
                ),
                "DISCORD_REJECTED",
                "rejected",
            ),
        )
        job = UploadJob(
            token="send",
            user_id=1,
            channel_id=2,
            guild_id=3,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
        )
        for error, code, outcome in cases:
            with (
                self.subTest(code=code, error=type(error).__name__),
                patch(
                    "professor_compressor.application.get_channel",
                    AsyncMock(return_value=Mock()),
                ),
                patch(
                    "professor_compressor.application.client.get_guild",
                    return_value=Mock(filesize_limit=20_000_000),
                ),
                patch(
                    "professor_compressor.application.send_result_batch",
                    AsyncMock(side_effect=error),
                ),
            ):
                with self.assertRaises(DeliveryFailure) as caught:
                    await deliver_browser_results(
                        job, [BrowserResult("clip.mp4", b"video")]
                    )
                self.assertEqual(caught.exception.error_code, code)
                self.assertEqual(caught.exception.outcome, outcome)
                self.assertNotIn("detail", str(caught.exception))

    def test_result_names_are_safe_and_bounded(self) -> None:
        result = safe_result_name("../" + "a" * 300 + ".mp4", 1)

        self.assertEqual(result, "a" * 120 + ".mp4")
        self.assertNotIn("/", result)

    async def test_results_are_sent_as_a_new_channel_message(self) -> None:
        interaction = Mock()
        job = UploadJob(
            token="token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=interaction,
            guild_id=789,
        )
        results = [BrowserResult(name="clip.mp4", data=b"video")]
        channel = Mock()
        channel.send = AsyncMock()

        with (
            patch(
                "professor_compressor.application.client.get_guild",
                return_value=Mock(filesize_limit=20_000_000),
            ),
            patch(
                "professor_compressor.application.get_channel",
                AsyncMock(return_value=channel),
            ),
            patch(
                "professor_compressor.application.send_result_batch",
                AsyncMock(),
            ) as send_batch,
        ):
            response = await deliver_browser_results(job, results)

        send_batch.assert_awaited_once_with(
            channel.send,
            results,
            "🎬 **Clip from** <@123>",
        )
        self.assertEqual(
            response,
            "Your compressed videos were delivered to Discord.",
        )
        self.assertFalse(interaction.followup.send.called)

    async def test_caption_can_ping_people_but_not_everyone_or_roles(self) -> None:
        job = UploadJob(
            token="caption-test",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            guild_id=789,
        )
        channel = Mock()
        channel.send = AsyncMock()
        results = [
            BrowserResult(name="clip-1.mp4", data=b"video"),
            BrowserResult(name="clip-2.mp4", data=b"video"),
        ]
        with (
            patch(
                "professor_compressor.application.client.get_guild",
                return_value=Mock(filesize_limit=20_000_000),
            ),
            patch(
                "professor_compressor.application.get_channel",
                AsyncMock(return_value=channel),
            ),
        ):
            for caption, quoted in (
                (
                    "Clips from yesterday @everyone <@456>",
                    "> Clips from yesterday @everyone <@456>",
                ),
                ("First line\n\nLast line", "> First line\n> \n> Last line"),
                ("x" * 1000, "> " + "x" * 1000),
            ):
                with self.subTest(caption_length=len(caption)):
                    await deliver_browser_results(job, results, caption)
                    sent = channel.send.await_args.kwargs
                    self.assertEqual(
                        sent["content"], "🎬 **Clips from** <@123>\n\n" + quoted
                    )
                    self.assertLessEqual(len(sent["content"]), 2000)
                    self.assertFalse(sent["allowed_mentions"].everyone)
                    self.assertFalse(sent["allowed_mentions"].roles)
                    self.assertTrue(sent["allowed_mentions"].users)


if __name__ == "__main__":
    unittest.main()
