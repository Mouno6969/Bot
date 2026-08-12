import unittest

from messenger_bot.messenger import count_messages, format_stats
from messenger_bot.router import RequestKind, parse_request


# Two scroll captures that OVERLAP: the same three messages appear in both, exactly
# as the real transcript stitches them. A correct counter must not double-count.
OVERLAPPING_SCRAPE = (
    "Enter, Message sent 20:00 by Alice: first\n"
    "Enter, Message sent 20:01 by Bob: second\n"
    "Enter, Message sent 20:02 by Alice: third\n"
    # duplicate capture of the same three messages:
    "Enter, Message sent 20:00 by Alice: first\n"
    "Enter, Message sent 20:01 by Bob: second\n"
    "Enter, Message sent 20:02 by Alice: third\n"
    # a boundary artifact line with no message text:
    "Enter, Message sent 20:03 by Bob\n"
    # the bot's own account:
    "Enter, Message sent 20:04 by You: replying\n"
)


class CountMessagesTests(unittest.TestCase):
    def test_overlapping_captures_are_not_double_counted(self):
        total, ranking = count_messages(OVERLAPPING_SCRAPE)
        # 3 distinct member messages + 1 "You" reply = 4 distinct messages.
        self.assertEqual(total, 4)
        counts = dict(ranking)
        self.assertEqual(counts["Alice"], 2)
        self.assertEqual(counts["Bob"], 1)
        self.assertEqual(counts["You"], 1)

    def test_boundary_line_without_text_is_not_counted(self):
        total, ranking = count_messages("Enter, Message sent 20:03 by Bob\n")
        self.assertEqual(total, 0)
        self.assertEqual(ranking, [])

    def test_ranking_is_sorted_by_count_desc(self):
        _, ranking = count_messages(OVERLAPPING_SCRAPE)
        counts = [c for _, c in ranking]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_two_identical_messages_same_minute_collapse(self):
        # Same (time, sender, text) can only be counted once — the honest choice given
        # the scrape cannot distinguish a real repeat from an overlap artifact.
        scrape = (
            "Enter, Message sent 20:05 by Alice: 😔\n"
            "Enter, Message sent 20:05 by Alice: 😔\n"
        )
        total, _ = count_messages(scrape)
        self.assertEqual(total, 1)

    def test_empty_transcript(self):
        self.assertEqual(count_messages(""), (0, []))


class FormatStatsTests(unittest.TestCase):
    def test_reports_total_and_member_ranking(self):
        out = format_stats(OVERLAPPING_SCRAPE, "Shahidulla Kaysar")
        self.assertIn("4 ta message", out)
        self.assertIn("1. Alice", out)
        self.assertIn("2. Bob", out)

    def test_bot_own_replies_reported_separately_not_ranked(self):
        out = format_stats(OVERLAPPING_SCRAPE, "Shahidulla Kaysar")
        # "You" must not occupy a ranked position, but is acknowledged separately.
        self.assertNotIn("You —", out)
        self.assertIn("Ami nije", out)

    def test_empty_transcript_gives_friendly_message(self):
        out = format_stats("", "Shahidulla Kaysar")
        self.assertIn("Ekhono", out)


class CalculateRoutingTests(unittest.TestCase):
    def test_calculate_and_stats_alias_route_to_calculate(self):
        self.assertEqual(parse_request("@Shahidulla /calculate").kind, RequestKind.CALCULATE)
        self.assertEqual(parse_request("@Shahidulla /stats").kind, RequestKind.CALCULATE)
        self.assertEqual(parse_request("@Shahidulla /CALCULATE now").kind, RequestKind.CALCULATE)


if __name__ == "__main__":
    unittest.main()
