import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from messenger_bot.messenger import MessengerBot


class FakePage:
    def __init__(self):
        self.reload_calls = []
        self.evaluate_calls = []

    async def reload(self, **kwargs):
        self.reload_calls.append(kwargs)

    async def evaluate(self, script, *args):
        self.evaluate_calls.append(script)
        return None


class MonitorRefreshTests(unittest.TestCase):
    def test_refresh_reloads_reauthenticates_and_restores_latest_view(self):
        bot = object.__new__(MessengerBot)
        bot.settings = type("SettingsStub", (), {"encryption_pin": None})()
        bot.last_observed_text = "stale"
        page = FakePage()

        with patch.object(bot, "_page_text", new=AsyncMock(return_value="fresh page text")):
            asyncio.run(bot._refresh_monitor_page(page))

        self.assertEqual(len(page.reload_calls), 1)
        self.assertEqual(page.reload_calls[0]["wait_until"], "commit")
        self.assertEqual(bot.last_observed_text, "fresh page text")
        self.assertEqual(len(page.evaluate_calls), 2)


if __name__ == "__main__":
    unittest.main()
