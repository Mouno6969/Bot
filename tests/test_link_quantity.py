import unittest

from messenger_bot.messenger import BotState, LinkVisitResult, MessengerBot


class LinkQuantityHandlerTests(unittest.IsolatedAsyncioTestCase):
    def _bot(self):
        bot = object.__new__(MessengerBot)
        bot.state = BotState()
        bot.state.save = lambda path: None
        bot.settings = type("Settings", (), {"state_file": None})()
        bot._reply_anchor = lambda recent: ""
        bot.sent = []
        bot.visits = []

        async def send_text(page, text, reply_to=None):
            bot.sent.append(text)

        async def visit(page, url, option_path, *, submit_requested=False, confirm_submit=False):
            bot.visits.append((url, option_path, submit_requested, confirm_submit))
            status = "awaiting_confirmation" if submit_requested and not confirm_submit else "submitted"
            return LinkVisitResult(status, url, option_path)

        bot._send_text = send_text
        bot._visit_facebook_link = visit
        return bot

    async def test_quantity_is_saved_during_pre_submit_step(self):
        bot = self._bot()
        url = "https://www.facebook.com/example.user"

        await bot._handle_request(
            None,
            "",
            f"@Shahidulla /link {url} 2 4 1 quantity 3 submit",
        )

        self.assertEqual(bot.visits, [(url, (2, 4, 1), True, False)])
        self.assertEqual(bot.state.pending_link_url, url)
        self.assertEqual(bot.state.pending_link_path, (2, 4, 1))
        self.assertEqual(bot.state.pending_link_quantity, 3)
        self.assertIn("quantity 3", bot.sent[-1])

    async def test_confirm_repeats_the_existing_flow_exactly(self):
        bot = self._bot()
        url = "https://www.facebook.com/example.user"
        bot.state.pending_link_url = url
        bot.state.pending_link_path = (2, 4, 1)
        bot.state.pending_link_quantity = 3
        bot.state.pending_link_sender = "Shahidulla"
        bot.state.pending_link_created_at = 1e20

        await bot._handle_request(None, "", "Message sent 10:00 by Shahidulla: @Shahidulla /link confirm")

        self.assertEqual(
            bot.visits,
            [
                (url, (2, 4, 1), True, True),
                (url, (2, 4, 1), True, True),
                (url, (2, 4, 1), True, True),
            ],
        )
        self.assertEqual(bot.state.pending_link_url, "")
        self.assertEqual(bot.state.pending_link_path, ())
        self.assertEqual(bot.state.pending_link_quantity, 0)
        self.assertIn("3 ta complete", bot.sent[-1])

    async def test_quantity_above_cap_is_rejected_before_visit(self):
        bot = self._bot()
        url = "https://www.facebook.com/example.user"

        await bot._handle_request(
            None,
            "",
            f"Message sent 10:00 by Shahidulla: @Shahidulla /link {url} 2 quantity 11 submit",
        )

        self.assertEqual(bot.visits, [])
        self.assertIn("maximum 10", bot.sent[-1])

    async def test_confirmation_from_different_sender_is_rejected(self):
        bot = self._bot()
        bot.state.pending_link_url = "https://www.facebook.com/example.user"
        bot.state.pending_link_path = (2, 4)
        bot.state.pending_link_quantity = 1
        bot.state.pending_link_sender = "Shahidulla"
        bot.state.pending_link_created_at = 1e20

        await bot._handle_request(None, "", "Message sent 10:00 by Other Person: @Shahidulla /link confirm")

        self.assertEqual(bot.visits, [])
        self.assertIn("sender", bot.sent[-1])

    async def test_expired_confirmation_is_cleared(self):
        bot = self._bot()
        bot.state.pending_link_url = "https://www.facebook.com/example.user"
        bot.state.pending_link_path = (2, 4)
        bot.state.pending_link_quantity = 1
        bot.state.pending_link_sender = "Shahidulla"
        bot.state.pending_link_created_at = 1.0

        await bot._handle_request(None, "", "Message sent 10:00 by Shahidulla: @Shahidulla /link confirm")

        self.assertEqual(bot.visits, [])
        self.assertEqual(bot.state.pending_link_url, "")
        self.assertEqual(bot.state.pending_link_quantity, 0)
        self.assertIn("shomoy shesh", bot.sent[-1])


if __name__ == "__main__":
    unittest.main()

