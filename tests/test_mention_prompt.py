import asyncio
import unittest

from messenger_bot.messenger import MessengerBot, _CHAT_PROMPT_MAX_CHARACTERS


class CaptureMedia:
    def __init__(self):
        self.prompt = None

    async def reply(self, prompt: str) -> str:
        self.prompt = prompt
        return "ok"


class DisabledMeta:
    enabled = False


class MentionPromptTests(unittest.TestCase):
    def test_long_transcript_is_bounded_before_manus_call(self):
        bot = object.__new__(MessengerBot)
        bot.settings = type("SettingsStub", (), {"bot_name": "Shahidulla Kaysar"})()
        bot.transcript = (
            "Message sent 10:00 by Alice: old conversation\n" + "x" * 50000 + "\n"
            "Message sent 10:05 by Bob: newest question"
        )
        bot.meta = DisabledMeta()
        bot.media = CaptureMedia()

        answer = asyncio.run(bot._answer_mention(bot.transcript))

        self.assertEqual(answer, "ok")
        self.assertIsNotNone(bot.media.prompt)
        self.assertLessEqual(len(bot.media.prompt), _CHAT_PROMPT_MAX_CHARACTERS)
        self.assertIn("newest question", bot.media.prompt)
        self.assertNotIn("old conversation", bot.media.prompt)


if __name__ == "__main__":
    unittest.main()
