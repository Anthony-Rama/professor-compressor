import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DISCORD_TOKEN", "test-token")

from professor_compressor import application as app


class CompressCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_interaction(self) -> Mock:
        interaction = Mock()
        interaction.guild_id = 123
        interaction.channel_id = 456
        interaction.guild.name = "Test server"
        interaction.user.id = 789
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
            patch.object(app.client, "get_guild", return_value=Mock()),
            patch.object(app, "jobs", {}),
            patch.object(app, "last_job_at", {}),
            patch.object(app, "schedule_owner_alert"),
        ):
            await app.compress.callback(interaction)
            self.assertEqual(len(app.jobs), 1)
            self.assertEqual(next(iter(app.jobs.values())).guild_id, 123)

        interaction.response.send_message.assert_awaited_once()

    def test_command_is_server_install_only(self) -> None:
        self.assertTrue(app.compress.allowed_installs.guild)
        self.assertFalse(app.compress.allowed_installs.user)


if __name__ == "__main__":
    unittest.main()
