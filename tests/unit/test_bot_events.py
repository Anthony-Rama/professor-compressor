from unittest.mock import Mock, patch

from tests.support.runtime import RuntimeTestCase


class GuildEventTests(RuntimeTestCase):
    async def test_join_event_schedules_notification(self) -> None:
        guild = Mock(name="guild")
        guild.name = "Test Server"
        guild.id = 123
        guild.member_count = 42

        with (
            patch.object(self.runtime.alerts, "schedule") as schedule_alert,
            patch.object(self.runtime.dsc_stats, "schedule_update") as schedule_stats,
        ):
            await self.runtime.guild_events.on_guild_join(guild)

        schedule_alert.assert_called_once()
        schedule_stats.assert_called_once_with()
        self.assertIn("installed", schedule_alert.call_args.args[0])
