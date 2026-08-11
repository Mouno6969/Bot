import unittest

from messenger_bot.messenger import MessengerBot


class MessageDeltaTests(unittest.TestCase):
    def test_no_delta_for_unchanged_page(self):
        self.assertEqual(MessengerBot._newly_appended_text("old", "old"), "")

    def test_only_appended_text_is_returned(self):
        self.assertEqual(
            MessengerBot._newly_appended_text("Earlier history", "Earlier history\n@Shahidulla /image cat"),
            "\n@Shahidulla /image cat",
        )

    def test_dom_rewrite_uses_bounded_tail(self):
        current = "x" * 600
        self.assertEqual(MessengerBot._newly_appended_text("different", current), "x" * 500)


if __name__ == "__main__":
    unittest.main()
