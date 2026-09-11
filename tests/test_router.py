import unittest

from messenger_bot.router import (
    GAME_KINDS,
    RequestKind,
    games_help_text,
    has_mention,
    is_facebook_url,
    parse_link_selection,
    parse_request,
)


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

    def test_link_command_and_facebook_url_validation(self):
        result = parse_request("@Shahidulla /link https://www.facebook.com/example.user")
        self.assertEqual(result.kind, RequestKind.LINK)
        self.assertEqual(result.argument, "https://www.facebook.com/example.user")
        self.assertTrue(is_facebook_url(result.argument))
        self.assertTrue(is_facebook_url("https://m.facebook.com/profile.php?id=123"))
        self.assertFalse(is_facebook_url("http://www.facebook.com/example.user"))
        self.assertFalse(is_facebook_url("https://example.com/redirect"))
        self.assertFalse(is_facebook_url("javascript:alert(1)"))

    def test_link_option_paths_are_limited_to_one_through_ten(self):
        url = "https://www.facebook.com/example.user"
        for option_number in range(1, 11):
            self.assertEqual(
                parse_link_selection(f"{url} {option_number}"),
                (url, (option_number,), False, 1),
            )
        self.assertEqual(parse_link_selection(url), (url, (1,), False, 1))
        self.assertEqual(parse_link_selection(f"{url} 2 4 1 submit"), (url, (2, 4, 1), True, 1))
        self.assertEqual(
            parse_link_selection(f"{url} 2 4 1"),
            (url, (2, 4, 1), False, 1),
        )
        self.assertEqual(
            parse_link_selection(f"{url} 2 4 1 quantity 3 submit"),
            (url, (2, 4, 1), True, 3),
        )
        self.assertEqual(
            parse_link_selection(f"{url} 2 quantity 4"),
            (url, (2,), False, 4),
        )
        self.assertIsNone(parse_link_selection(f"{url} 0"))
        self.assertIsNone(parse_link_selection(f"{url} 2 11"))
        self.assertIsNone(parse_link_selection(f"{url} 2 4 0"))
        self.assertIsNone(parse_link_selection(f"{url} 2 ten"))
        self.assertIsNone(parse_link_selection(f"{url} submit 2"))
        self.assertIsNone(parse_link_selection(f"{url} 2 quantity"))
        self.assertEqual(parse_link_selection(f"{url} 2 quantity 11"), (url, (2,), False, 11))
        self.assertEqual(parse_link_selection(f"{url} 2 quantity 1000"), (url, (2,), False, 1000))
        self.assertIsNone(parse_link_selection(f"{url} 2 quantity 0"))
        self.assertIsNone(parse_link_selection(f"{url} 2 quantity 3 extra"))

    def test_link_confirm_is_parsed_without_a_url(self):
        result = parse_request("@Shahidulla Kaysar /link confirm")
        self.assertEqual(result.kind, RequestKind.LINK)
        self.assertEqual(result.argument, "confirm")

    def test_normal_mention_remains_chat(self):
        result = parse_request("@Shahidulla who has communicated most clearly?")
        self.assertEqual(result.kind, RequestKind.CHAT)

    def test_game_commands_are_parsed(self):
        expected = {
            "/games": RequestKind.GAMES,
            "/quiz": RequestKind.QUIZ,
            "/quiz sports": (RequestKind.QUIZ, "sports"),
            "/answer B": (RequestKind.ANSWER, "B"),
            "/race": RequestKind.RACE,
            "/race 3": (RequestKind.RACE, "3"),
            "/race go": (RequestKind.RACE, "go"),
            "/guess": RequestKind.GUESS,
            "/guess 27": (RequestKind.GUESS, "27"),
            "/flip heads 50": (RequestKind.FLIP, "heads 50"),
            "/balance": RequestKind.BALANCE,
            "/credits": RequestKind.BALANCE,
            "/wallet": RequestKind.BALANCE,
            "/ranking": RequestKind.RANKING,
            "/leaderboard": RequestKind.RANKING,
            "/rank": RequestKind.RANKING,
            "/daily": RequestKind.DAILY,
        }
        for line, want in expected.items():
            result = parse_request(f"@Shahidulla {line}")
            if isinstance(want, tuple):
                self.assertEqual(result.kind, want[0], line)
                self.assertEqual(result.argument, want[1], line)
            else:
                self.assertEqual(result.kind, want, line)
                self.assertEqual(result.argument, "", line)
        # Every non-help game kind routes to the local game engine.
        for kind in expected.values():
            if isinstance(kind, tuple):
                kind = kind[0]
            if kind != RequestKind.GAMES:
                self.assertIn(kind, GAME_KINDS)

    def test_game_argument_stops_at_end_of_command_line(self):
        scrape = (
            "@Shahidulla /flip tails 100\n"
            "Compose\n"
            "Chat members\n"
            "Privacy & support\n"
        )
        result = parse_request(scrape)
        self.assertEqual(result.kind, RequestKind.FLIP)
        self.assertEqual(result.argument, "tails 100")

    def test_games_help_lists_every_game_command(self):
        text = games_help_text()
        for command in ("/quiz", "/answer", "/race", "/guess", "/flip", "/daily", "/balance", "/ranking"):
            self.assertIn(command, text)

    def test_argument_stops_at_end_of_command_line(self):
        # Messenger's accessibility scrape appends UI chrome after the message.
        # The argument must be only what the user typed on the command line,
        # never the trailing "Compose / Chat members / Privacy & support" junk.
        scrape = (
            "@Shahidulla Kaysar /voice sobai ke shubho sokal janai\n"
            "Compose\n"
            "Write to BGC (Body)\n"
            "Chat Info\n"
            "Customise chat\n"
            "Chat members\n"
            "Media, files and links\n"
            "Privacy & support\n"
        )
        result = parse_request(scrape)
        self.assertEqual(result.kind, RequestKind.VOICE)
        self.assertEqual(result.argument, "sobai ke shubho sokal janai")

    def test_image_argument_also_bounded_to_line(self):
        scrape = "@Shahidulla /image a rainy Dhaka street\nCompose\nChat members\nPrivacy & support"
        result = parse_request(scrape)
        self.assertEqual(result.kind, RequestKind.IMAGE)
        self.assertEqual(result.argument, "a rainy Dhaka street")

    def test_sing_argument_bounded_and_after_sender_prefix(self):
        scrape = (
            "Enter, Message sent 03:45 by Mouno (bideshi Kamla): "
            "@Shahidulla Kaysar /sing ekta friendship gaan bol\n"
            "Compose\nChat members\n"
        )
        result = parse_request(scrape)
        self.assertEqual(result.kind, RequestKind.SING)
        self.assertEqual(result.argument, "ekta friendship gaan bol")


if __name__ == "__main__":
    unittest.main()
