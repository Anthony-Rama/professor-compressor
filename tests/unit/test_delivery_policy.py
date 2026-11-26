import unittest
from unittest.mock import Mock

import discord

from professor_compressor.bot.delivery_policy import (
    UNCONFIRMED_DELIVERY_MESSAGE,
    DeliveryFailure,
    bot_upload_limit,
    channel_delivery_problem,
    compression_target,
    delivery_failure,
)


class DeliveryFailureTests(unittest.TestCase):
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


class DeliveryPolicyTests(unittest.TestCase):
    def test_base_override_and_boosted_allowance(self):
        self.assertEqual(
            bot_upload_limit(Mock(filesize_limit=10 * 1024 * 1024)), 20 * 1024 * 1024
        )
        self.assertEqual(
            bot_upload_limit(Mock(filesize_limit=100 * 1024 * 1024)), 100 * 1024 * 1024
        )
        self.assertEqual(bot_upload_limit(Mock(filesize_limit=10), 123), 123)

    def test_threads_use_thread_permission_and_reject_archived_or_unjoined(self):
        thread = Mock(spec=discord.Thread)
        thread.archived = False
        thread.is_private.return_value = False
        permissions = discord.Permissions(
            view_channel=True, attach_files=True, send_messages=True
        )
        self.assertIn(
            "Send Messages in Threads", channel_delivery_problem(thread, permissions)
        )
        permissions.send_messages_in_threads = True
        self.assertIsNone(channel_delivery_problem(thread, permissions))
        thread.archived = True
        self.assertIn("Unarchive", channel_delivery_problem(thread, permissions))
        thread.archived = False
        thread.is_private.return_value = True
        thread.me = None
        self.assertIn("private thread", channel_delivery_problem(thread, permissions))
