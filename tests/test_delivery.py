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


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def test_compression_target_keeps_small_delivery_reserve(self) -> None:
        self.assertEqual(compression_target(20_000_000), 19_600_000)

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
        )
        results = [BrowserResult(name="clip.mp4", data=b"video")]
        channel = Mock()
        channel.send = AsyncMock()

        with (
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
            "✅ **Compression complete!** <@123>, your compressed video is ready.",
        )
        self.assertEqual(
            response,
            "Your compressed videos were delivered to Discord.",
        )
        self.assertFalse(interaction.followup.send.called)


if __name__ == "__main__":
    unittest.main()
