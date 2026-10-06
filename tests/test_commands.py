import os
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-token")

import discord

from professor_compressor import application as app
from professor_compressor.domain import JobState, UploadJob


class CompressCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_interaction(self) -> Mock:
        interaction = Mock()
        interaction.guild_id = 123
        interaction.channel_id = 456
        interaction.guild.name = "Test server"
        interaction.guild.filesize_limit = 20_000_000
        interaction.app_permissions = discord.Permissions(
            view_channel=True, send_messages=True, attach_files=True
        )
        interaction.user.id = 789
        interaction.user.name = "clip_creator"
        interaction.filesize_limit = 20_000_000
        interaction.response.send_message = AsyncMock()
        return interaction

    async def test_missing_server_install_does_not_create_session(self) -> None:
        interaction = self.make_interaction()
        with (
            patch.object(app.client, "get_guild", return_value=None),
            patch.object(app, "jobs", {}),
            patch.object(app, "last_job_at", {}),
            patch.object(app, "schedule_owner_alert") as alert,
        ):
            await app.compress.callback(interaction)
            self.assertEqual(app.jobs, {})
            self.assertEqual(app.last_job_at, {})

        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("installed in this server", message)
        self.assertIn("Manage Server", message)
        self.assertTrue(
            interaction.response.send_message.await_args.kwargs["ephemeral"]
        )
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].url, app.BOT_INVITE_URL)
        alert.assert_not_called()

    async def test_server_install_creates_session(self) -> None:
        interaction = self.make_interaction()
        with (
            patch.object(app.client, "get_guild", return_value=interaction.guild),
            patch.object(app, "jobs", {}),
            patch.object(app, "last_job_at", {}),
            patch.object(app, "schedule_owner_alert") as alert,
        ):
            await app.compress.callback(interaction)
            self.assertEqual(len(app.jobs), 1)
            self.assertEqual(next(iter(app.jobs.values())).guild_id, 123)
            self.assertIn("Username: **@clip\\_creator**", alert.call_args.args[0])
            self.assertIn("User ID: `789`", alert.call_args.args[0])
            self.assertEqual(next(iter(app.jobs.values())).username, "clip_creator")
            self.assertNotIn("<@789>", alert.call_args.args[0])

        interaction.response.send_message.assert_awaited_once()
        response_text = interaction.response.send_message.await_args.args[0]
        self.assertIn("Compression runs locally.", response_text)
        self.assertNotIn("fitting MP4s", response_text)

    async def test_user_nitro_allowance_does_not_set_bot_upload_limit(self):
        interaction = self.make_interaction()
        interaction.filesize_limit = 500_000_000
        with (
            patch.object(app.client, "get_guild", return_value=interaction.guild),
            patch.object(app, "jobs", {}),
            patch.object(app, "last_job_at", {}),
            patch.object(app, "schedule_owner_alert"),
        ):
            await app.compress.callback(interaction)
            self.assertEqual(
                next(iter(app.jobs.values())).discord_limit, 20 * 1024 * 1024
            )

    async def test_missing_channel_permissions_prevent_session(self):
        for permission in ("view_channel", "send_messages", "attach_files"):
            interaction = self.make_interaction()
            setattr(interaction.app_permissions, permission, False)
            with (
                patch.object(app.client, "get_guild", return_value=interaction.guild),
                patch.object(app, "jobs", {}),
            ):
                await app.compress.callback(interaction)
                self.assertFalse(app.jobs)
                self.assertIn(
                    "permissions", interaction.response.send_message.await_args.args[0]
                )

    async def test_active_session_is_not_replaced_but_abandoned_one_can_be(self):
        for active in (True, False):
            interaction = self.make_interaction()
            job = UploadJob(
                "original",
                789,
                456,
                time.time() + 600,
                20_000_000,
                interaction,
                state=JobState.CLAIMED,
                processing_until=time.time() + 90 if active else 0,
            )
            with (
                patch.object(app.client, "get_guild", return_value=interaction.guild),
                patch.object(app, "jobs", {job.token: job}),
                patch.object(app, "last_job_at", {}),
                patch.object(app, "schedule_owner_alert"),
            ):
                await app.compress.callback(interaction)
                self.assertEqual(job.token in app.jobs, active)

    async def test_page_exit_grace_unlocks_stale_processing_session(self):
        for seconds_since_exit, blocked in ((1, True), (6, False)):
            interaction = self.make_interaction()
            job = UploadJob(
                "original",
                789,
                456,
                time.time() + 600,
                20_000_000,
                interaction,
                state=JobState.CLAIMED,
                processing_until=time.time() + 90,
                page_left_at=time.time() - seconds_since_exit,
            )
            with (
                patch.object(app.client, "get_guild", return_value=interaction.guild),
                patch.object(app, "jobs", {job.token: job}),
                patch.object(app, "last_job_at", {}),
                patch.object(app, "schedule_owner_alert"),
            ):
                await app.compress.callback(interaction)
                self.assertEqual(job.token in app.jobs, blocked)
                if blocked:
                    self.assertIn(
                        "wait 4 seconds",
                        interaction.response.send_message.await_args.args[0],
                    )

    async def test_queued_delivery_cannot_be_replaced_after_page_exit(self):
        interaction = self.make_interaction()
        job = UploadJob(
            "original",
            789,
            456,
            time.time() + 600,
            20_000_000,
            interaction,
            state=JobState.QUEUED,
            page_left_at=time.time() - 60,
        )
        with (
            patch.object(app.client, "get_guild", return_value=interaction.guild),
            patch.object(app, "jobs", {job.token: job}),
            patch.object(app, "last_job_at", {}),
        ):
            await app.compress.callback(interaction)
            self.assertIn("original", app.jobs)
            self.assertIn(
                "sending files", interaction.response.send_message.await_args.args[0]
            )

    async def test_exit_wait_includes_remaining_command_cooldown(self):
        interaction = self.make_interaction()
        job = UploadJob(
            "original",
            789,
            456,
            time.time() + 600,
            20_000_000,
            interaction,
            state=JobState.CLAIMED,
            processing_until=time.time() + 90,
            page_left_at=time.time() - 1,
        )
        with (
            patch.object(app.client, "get_guild", return_value=interaction.guild),
            patch.object(app, "jobs", {job.token: job}),
            patch.object(app, "last_job_at", {(123, 789): time.time() - 2}),
        ):
            await app.compress.callback(interaction)
            self.assertIn(
                "wait 13 seconds", interaction.response.send_message.await_args.args[0]
            )

    async def test_drain_rejects_new_command(self):
        interaction = self.make_interaction()
        with patch.object(app, "draining", True), patch.object(app, "jobs", {}):
            await app.compress.callback(interaction)
            self.assertFalse(app.jobs)
            self.assertIn(
                "maintenance", interaction.response.send_message.await_args.args[0]
            )

    def test_command_is_server_install_only(self) -> None:
        self.assertTrue(app.compress.allowed_installs.guild)
        self.assertFalse(app.compress.allowed_installs.user)


if __name__ == "__main__":
    unittest.main()
