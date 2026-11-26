import time
from unittest.mock import AsyncMock, Mock, patch

import discord

from professor_compressor.branding import BOT_INVITE_URL
from professor_compressor.domain import JobState, UploadJob
from tests.support.runtime import RuntimeTestCase


class CompressCommandTests(RuntimeTestCase):
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
            patch.object(self.runtime.client, "get_guild", return_value=None),
            patch.object(self.runtime.sessions, "jobs", {}),
            patch.object(self.runtime.sessions, "last_job_at", {}),
            patch.object(self.runtime.alerts, "schedule") as alert,
        ):
            await self.runtime.compress(interaction)
            self.assertEqual(self.runtime.sessions.jobs, {})
            self.assertEqual(self.runtime.sessions.last_job_at, {})

        message = interaction.response.send_message.await_args.args[0]
        self.assertIn("installed in this server", message)
        self.assertIn("Manage Server", message)
        self.assertTrue(
            interaction.response.send_message.await_args.kwargs["ephemeral"]
        )
        view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(view.children[0].url, BOT_INVITE_URL)
        alert.assert_not_called()

    async def test_server_install_creates_session(self) -> None:
        interaction = self.make_interaction()
        with (
            patch.object(
                self.runtime.client, "get_guild", return_value=interaction.guild
            ),
            patch.object(self.runtime.sessions, "jobs", {}),
            patch.object(self.runtime.sessions, "last_job_at", {}),
            patch.object(self.runtime.alerts, "schedule") as alert,
        ):
            await self.runtime.compress(interaction)
            self.assertEqual(len(self.runtime.sessions.jobs), 1)
            self.assertEqual(
                next(iter(self.runtime.sessions.jobs.values())).guild_id, 123
            )
            self.assertIn("Username: **@clip\\_creator**", alert.call_args.args[0])
            self.assertIn("User ID: `789`", alert.call_args.args[0])
            self.assertEqual(
                next(iter(self.runtime.sessions.jobs.values())).username, "clip_creator"
            )
            self.assertNotIn("<@789>", alert.call_args.args[0])

        interaction.response.send_message.assert_awaited_once()
        response_text = interaction.response.send_message.await_args.args[0]
        self.assertIn("Compression runs locally.", response_text)
        self.assertNotIn("fitting MP4s", response_text)

    async def test_user_nitro_allowance_does_not_set_bot_upload_limit(self):
        interaction = self.make_interaction()
        interaction.filesize_limit = 500_000_000
        with (
            patch.object(
                self.runtime.client, "get_guild", return_value=interaction.guild
            ),
            patch.object(self.runtime.sessions, "jobs", {}),
            patch.object(self.runtime.sessions, "last_job_at", {}),
            patch.object(self.runtime.alerts, "schedule"),
        ):
            await self.runtime.compress(interaction)
            self.assertEqual(
                next(iter(self.runtime.sessions.jobs.values())).discord_limit,
                20 * 1024 * 1024,
            )

    async def test_missing_channel_permissions_prevent_session(self):
        for permission in ("view_channel", "send_messages", "attach_files"):
            interaction = self.make_interaction()
            setattr(interaction.app_permissions, permission, False)
            with (
                patch.object(
                    self.runtime.client, "get_guild", return_value=interaction.guild
                ),
                patch.object(self.runtime.sessions, "jobs", {}),
            ):
                await self.runtime.compress(interaction)
                self.assertFalse(self.runtime.sessions.jobs)
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
                patch.object(
                    self.runtime.client, "get_guild", return_value=interaction.guild
                ),
                patch.object(self.runtime.sessions, "jobs", {job.token: job}),
                patch.object(self.runtime.sessions, "last_job_at", {}),
                patch.object(self.runtime.alerts, "schedule"),
            ):
                await self.runtime.compress(interaction)
                self.assertEqual(job.token in self.runtime.sessions.jobs, active)

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
                patch.object(
                    self.runtime.client, "get_guild", return_value=interaction.guild
                ),
                patch.object(self.runtime.sessions, "jobs", {job.token: job}),
                patch.object(self.runtime.sessions, "last_job_at", {}),
                patch.object(self.runtime.alerts, "schedule"),
            ):
                await self.runtime.compress(interaction)
                self.assertEqual(job.token in self.runtime.sessions.jobs, blocked)
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
            patch.object(
                self.runtime.client, "get_guild", return_value=interaction.guild
            ),
            patch.object(self.runtime.sessions, "jobs", {job.token: job}),
            patch.object(self.runtime.sessions, "last_job_at", {}),
        ):
            await self.runtime.compress(interaction)
            self.assertIn("original", self.runtime.sessions.jobs)
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
            patch.object(
                self.runtime.client, "get_guild", return_value=interaction.guild
            ),
            patch.object(self.runtime.sessions, "jobs", {job.token: job}),
            patch.object(
                self.runtime.sessions, "last_job_at", {(123, 789): time.time() - 2}
            ),
        ):
            await self.runtime.compress(interaction)
            self.assertIn(
                "wait 13 seconds", interaction.response.send_message.await_args.args[0]
            )

    async def test_drain_rejects_new_command(self):
        interaction = self.make_interaction()
        with (
            patch.object(self.runtime.sessions, "draining", True),
            patch.object(self.runtime.sessions, "jobs", {}),
        ):
            await self.runtime.compress(interaction)
            self.assertFalse(self.runtime.sessions.jobs)
            self.assertIn(
                "maintenance", interaction.response.send_message.await_args.args[0]
            )

    def test_command_is_server_install_only(self) -> None:
        command = self.runtime.tree.get_command("compress")
        assert command is not None
        installs = command.allowed_installs
        assert installs is not None
        self.assertTrue(installs.guild)
        self.assertFalse(installs.user)
