from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from tests.support.runtime import RuntimeTestCase


class DscStatsTests(RuntimeTestCase):
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
            patch.object(self.runtime.dsc_stats, "client", mock_client),
            patch.object(
                self.runtime.dsc_stats,
                "settings",
                replace(self.runtime.settings, dsc_api_token="test-api-token"),
            ),
            patch(
                "professor_compressor.integrations.dsc.ClientSession",
                return_value=session_context,
            ),
        ):
            self.assertTrue(await self.runtime.dsc_stats.report())

        session.post.assert_called_once()
        request = session.post.call_args
        self.assertEqual(
            request.args[0],
            "https://dsc.sh/api/bots/1550752400271351839/stats",
        )
        self.assertEqual(request.kwargs["json"], {"server_count": 3})
        self.assertEqual(request.kwargs["headers"]["Authorization"], "test-api-token")
        response.raise_for_status.assert_called_once_with()
