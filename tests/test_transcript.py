import unittest

from messenger_bot.messenger import MessengerBot

# Overlaps in these fixtures must be at least 20 characters: shorter shared
# edges are treated as distinct text to avoid false merges.
HEAD = "the quick brown fox jumps over "
TAIL = "the lazy dog near the river bank"
SHARED = "shared overlap boundary text"  # 26 chars


class TranscriptMergeTests(unittest.TestCase):
    def test_merge_newer_removes_overlap(self):
        base = HEAD + SHARED
        fragment = SHARED + TAIL
        self.assertEqual(MessengerBot._merge_newer(base, fragment), HEAD + SHARED + TAIL)

    def test_merge_older_removes_overlap(self):
        base = SHARED + TAIL
        fragment = HEAD + SHARED
        self.assertEqual(MessengerBot._merge_older(base, fragment), HEAD + SHARED + TAIL)

    def test_contained_fragment_is_ignored(self):
        base = HEAD + SHARED + TAIL
        self.assertEqual(MessengerBot._merge_newer(base, SHARED), base)
        self.assertEqual(MessengerBot._merge_older(base, SHARED), base)

    def test_larger_fragment_replaces_base(self):
        base = SHARED
        fragment = HEAD + SHARED + TAIL
        self.assertEqual(MessengerBot._merge_newer(base, fragment), fragment)
        self.assertEqual(MessengerBot._merge_older(base, fragment), fragment)

    def test_disjoint_fragments_join_with_newline(self):
        base = "completely separate first chunk of text"
        fragment = "another quite different second chunk"
        self.assertEqual(
            MessengerBot._merge_newer(base, fragment), base + "\n" + fragment
        )
        self.assertEqual(
            MessengerBot._merge_older(base, fragment), fragment + "\n" + base
        )

    def test_empty_inputs(self):
        base = "some existing transcript text"
        fragment = "some incoming fragment text"
        self.assertEqual(MessengerBot._merge_newer(base, ""), base)
        self.assertEqual(MessengerBot._merge_newer("", fragment), fragment)
        self.assertEqual(MessengerBot._merge_older(base, ""), base)
        self.assertEqual(MessengerBot._merge_older("", fragment), fragment)

    def test_short_overlap_is_not_merged(self):
        base = "first chunk ends with common"
        fragment = "common ground starts second chunk"
        merged = MessengerBot._merge_newer(base, fragment)
        self.assertEqual(merged, base + "\n" + fragment)


if __name__ == "__main__":
    unittest.main()
