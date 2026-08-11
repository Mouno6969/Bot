import unittest

from messenger_bot.router import RequestKind, has_mention, parse_request


class RouterTests(unittest.TestCase):
    def test_full_and_short_mentions_are_detected(self):
        self.assertTrue(has_mention("Hey Shahidulla Kaysar, help", "Shahidulla Kaysar"))
        self.assertTrue(has_mention("@Shahidulla help", "Shahidulla Kaysar"))
        self.assertFalse(has_mention("hello everyone", "Shahidulla Kaysar"))

    def test_media_commands_are_parsed(self):
        result = parse_request("@Shahidulla /image cinematic Dhaka in rain")
        self.assertEqual(result.kind, RequestKind.IMAGE)
        self.assertEqual(result.argument, "cinematic Dhaka in rain")

        result = parse_request("@Shahidulla /voice সবাই কেমন আছো?")
        self.assertEqual(result.kind, RequestKind.VOICE)
        self.assertEqual(result.argument, "সবাই কেমন আছো?")

        result = parse_request("@Shahidulla /sing 45-second Banglish friendship song")
        self.assertEqual(result.kind, RequestKind.SING)

        result = parse_request("@Shahidulla /edit make it watercolor")
        self.assertEqual(result.kind, RequestKind.EDIT)

    def test_normal_mention_remains_chat(self):
        result = parse_request("@Shahidulla who has communicated most clearly?")
        self.assertEqual(result.kind, RequestKind.CHAT)


if __name__ == "__main__":
    unittest.main()
