import unittest

from messenger_bot.messenger import extract_members


SCRAPE = (
    "Enter, Message sent 20:00 by kyare kamkoro: Game e dhukle\n"
    "Enter, Message sent 20:01 by kyare kamkoro: Eda theka shovel\n"
    "Enter, Message sent 20:02 by pocha bilai (Creepটোর Rawজা): Bainga lamo\n"
    "Enter, Message sent 20:04 by Mouno (bideshi Kamla): 😔\n"
    "Enter, Message sent 20:07 by Gaspump: Ish\n"
    "Enter, Message sent 20:08 by kyare kamkoro\n"  # a boundary line with no colon/text
    "Enter, Message sent 21:00 by You: replying\n"
)


class ExtractMembersTests(unittest.TestCase):
    def test_extracts_distinct_members_ranked_by_activity(self):
        members = extract_members(SCRAPE)
        # kyare kamkoro spoke most; every distinct human sender is present.
        self.assertEqual(members[0], "kyare kamkoro")
        self.assertIn("pocha bilai (Creepটোর Rawজা)", members)
        self.assertIn("Mouno (bideshi Kamla)", members)
        self.assertIn("Gaspump", members)

    def test_excludes_the_bots_own_you_account(self):
        self.assertNotIn("You", extract_members(SCRAPE))

    def test_names_are_whole_and_not_truncated_at_parentheses(self):
        # A prior heuristic split "pocha bilai (Creepটোর Rawজা)" into fragments.
        members = extract_members(SCRAPE)
        self.assertNotIn("Creepটোর Rawজা)", members)

    def test_boundary_line_without_text_still_counts_the_sender(self):
        # "…by kyare kamkoro" with no trailing ": text" must not be dropped.
        self.assertIn("kyare kamkoro", extract_members("Enter, Message sent 20:08 by kyare kamkoro\n"))

    def test_empty_transcript_yields_no_members(self):
        self.assertEqual(extract_members(""), [])

    def test_respects_max_members_cap(self):
        big = "".join(f"Enter, Message sent 10:0{i} by Person{i}: hi\n" for i in range(9))
        self.assertEqual(len(extract_members(big, max_members=3)), 3)


if __name__ == "__main__":
    unittest.main()
