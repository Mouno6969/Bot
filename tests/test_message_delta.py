import json
import tempfile
import unittest
from pathlib import Path

from messenger_bot.messenger import BotState, MessengerBot

BOT = "Shahidulla Kaysar"


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


class MentionFingerprintTests(unittest.TestCase):
    def test_same_mention_has_same_fingerprint_across_chunks(self):
        first = "Message sent 10:00 by Rahim: @Shahidulla /image cat\nCompose\n"
        second = (
            "Message sent 10:00 by Rahim: @Shahidulla /image cat\n"
            "Message sent 10:01 by You: HORSE RACE #1 (frame 4)\n"
        )
        self.assertEqual(
            MessengerBot._newest_mention_fingerprint(first, BOT),
            MessengerBot._newest_mention_fingerprint(second, BOT),
        )

    def test_bot_frames_after_the_mention_do_not_win(self):
        # The bot's own newest message must not become the dedupe id — otherwise
        # every new race frame would look like a fresh trigger.
        fragment = (
            "Message sent 10:00 by Rahim: @Shahidulla /answer B\n"
            "Message sent 10:01 by You: HORSE RACE #2 (frame 1)\n"
            "Message sent 10:02 by You: HORSE RACE #2 (frame 2)\n"
        )
        same_mention = "Message sent 10:00 by Rahim: @Shahidulla /answer B\n"
        self.assertEqual(
            MessengerBot._newest_mention_fingerprint(fragment, BOT),
            MessengerBot._newest_mention_fingerprint(same_mention, BOT),
        )

    def test_different_users_or_texts_get_different_fingerprints(self):
        one = MessengerBot._newest_mention_fingerprint(
            "Message sent 10:00 by Rahim: @Shahidulla /answer B", BOT
        )
        other_sender = MessengerBot._newest_mention_fingerprint(
            "Message sent 10:00 by Karim: @Shahidulla /answer B", BOT
        )
        other_text = MessengerBot._newest_mention_fingerprint(
            "Message sent 10:00 by Rahim: @Shahidulla /answer C", BOT
        )
        self.assertNotEqual(one, other_sender)
        self.assertNotEqual(one, other_text)

    def test_newest_mention_wins_over_older_ones(self):
        fragment = (
            "Message sent 10:00 by Rahim: @Shahidulla /quiz\n"
            "Message sent 10:01 by Karim: @Shahidulla /answer B\n"
        )
        single = "Message sent 10:01 by Karim: @Shahidulla /answer B\n"
        self.assertEqual(
            MessengerBot._newest_mention_fingerprint(fragment, BOT),
            MessengerBot._newest_mention_fingerprint(single, BOT),
        )

    def test_no_mention_returns_empty(self):
        self.assertEqual(
            MessengerBot._newest_mention_fingerprint("Message sent 10:00 by Rahim: hello group", BOT),
            "",
        )
        self.assertEqual(MessengerBot._newest_mention_fingerprint("", BOT), "")

    def test_mention_sender_ignores_trailing_bot_frames(self):
        fragment = (
            "Message sent 10:00 by Rahim: @Shahidulla /race 3\n"
            "Message sent 10:01 by You: HORSE RACE #5 (frame 2)\n"
        )
        self.assertEqual(MessengerBot._mention_sender(fragment, BOT), "Rahim")
        self.assertEqual(MessengerBot._mention_sender("no messages here", BOT), "")


class BotStateMessageDedupTests(unittest.TestCase):
    def test_processed_messages_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot_state.json"
            state = BotState()
            state.processed_messages.append("fp-one")
            state.processed_messages.append("fp-two")
            state.save(path)
            loaded = BotState.load(path)
            self.assertEqual(list(loaded.processed_messages), ["fp-one", "fp-two"])
            # Legacy state files without the new key still load.
            legacy = Path(tmp) / "legacy.json"
            legacy.write_text(json.dumps({"processed": [], "last_reply": ""}), encoding="utf-8")
            self.assertEqual(list(BotState.load(legacy).processed_messages), [])


if __name__ == "__main__":
    unittest.main()
