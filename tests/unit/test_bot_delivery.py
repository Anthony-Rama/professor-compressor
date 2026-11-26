from unittest.mock import AsyncMock, Mock, patch

import discord

from professor_compressor.bot import delivery as bot_delivery
from professor_compressor.bot.delivery_policy import (
    DeliveryFailure,
)
from professor_compressor.domain import BrowserResult, UploadJob
from tests.support.media import mp4
from tests.support.runtime import RelayTestCase, RuntimeTestCase


class BotDeliveryTests(RuntimeTestCase):
    async def test_missing_destination_is_a_confirmed_access_failure(self) -> None:
        job = UploadJob(
            token="missing",
            user_id=1,
            channel_id=2,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
        )
        with patch.object(
            self.runtime.delivery, "get_channel", AsyncMock(return_value=None)
        ):
            with self.assertRaises(DeliveryFailure) as caught:
                await self.runtime.delivery.deliver_browser_results(
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
                patch.object(
                    self.runtime.delivery,
                    "get_channel",
                    AsyncMock(return_value=Mock()),
                ),
                patch.object(
                    self.runtime.client,
                    "get_guild",
                    return_value=Mock(filesize_limit=20_000_000),
                ),
                patch.object(
                    self.runtime.delivery,
                    "send_result_batch",
                    AsyncMock(side_effect=error),
                ),
            ):
                with self.assertRaises(DeliveryFailure) as caught:
                    await self.runtime.delivery.deliver_browser_results(
                        job, [BrowserResult("clip.mp4", b"video")]
                    )
                self.assertEqual(caught.exception.error_code, code)
                self.assertEqual(caught.exception.outcome, outcome)
                self.assertNotIn("detail", str(caught.exception))

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
            patch.object(
                self.runtime.client,
                "get_guild",
                return_value=Mock(filesize_limit=20_000_000),
            ),
            patch.object(
                self.runtime.delivery,
                "get_channel",
                AsyncMock(return_value=channel),
            ),
            patch.object(
                self.runtime.delivery,
                "send_result_batch",
                AsyncMock(),
            ) as send_batch,
        ):
            response = await self.runtime.delivery.deliver_browser_results(job, results)

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
            patch.object(
                self.runtime.client,
                "get_guild",
                return_value=Mock(filesize_limit=20_000_000),
            ),
            patch.object(
                self.runtime.delivery,
                "get_channel",
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
                    await self.runtime.delivery.deliver_browser_results(
                        job, results, caption
                    )
                    sent = channel.send.await_args.kwargs
                    self.assertEqual(
                        sent["content"], "🎬 **Clips from** <@123>\n\n" + quoted
                    )
                    self.assertLessEqual(len(sent["content"]), 2000)
                    self.assertFalse(sent["allowed_mentions"].everyone)
                    self.assertFalse(sent["allowed_mentions"].roles)
                    self.assertTrue(sent["allowed_mentions"].users)


class BotDeliveryRecheckTests(RelayTestCase):
    async def test_delivery_rechecks_permissions_and_current_limit(self):
        job = self.job()
        guild = Mock(filesize_limit=20 * 1024 * 1024)
        channel = Mock()
        channel.send = AsyncMock()
        channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, send_messages=True, attach_files=False
        )
        with (
            patch.object(
                self.runtime.delivery, "get_channel", AsyncMock(return_value=channel)
            ),
            patch.object(self.runtime.client, "get_guild", return_value=guild),
        ):
            with self.assertRaisesRegex(RuntimeError, "Attach Files"):
                await self.runtime.delivery.deliver_browser_results(
                    job, [BrowserResult("a.mp4", mp4())]
                )
            channel.send.assert_not_awaited()
            channel.permissions_for.return_value.attach_files = True
            with patch.object(bot_delivery, "bot_upload_limit", return_value=1):
                with self.assertRaisesRegex(RuntimeError, "allowance changed"):
                    await self.runtime.delivery.deliver_browser_results(
                        job, [BrowserResult("a.mp4", mp4())]
                    )
            channel.send.assert_not_awaited()
