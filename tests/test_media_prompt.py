import unittest

from messenger_bot.media import ManusMediaClient
from messenger_bot.router import RequestKind


class VoicePromptTests(unittest.TestCase):
    def test_script_is_fenced_verbatim(self):
        script = "আজকে সবাই কেমন আছো?"
        prompt = ManusMediaClient._task_prompt(RequestKind.VOICE, script)
        # The user's script must be wrapped in the fence exactly once, and must
        # not be duplicated anywhere else in the prompt (the anti-leak invariant).
        self.assertIn(f"<script>\n{script}\n</script>", prompt)
        self.assertEqual(prompt.count(script), 1)
        # Exactly one closing fence keeps the delimited block well-formed.
        self.assertEqual(prompt.count("</script>"), 1)

    def test_instructions_forbid_reading_themselves(self):
        prompt = ManusMediaClient._task_prompt(RequestKind.VOICE, "hi").lower()
        self.assertIn("verbatim", prompt)
        self.assertIn("do not read these instructions", prompt)

    def test_no_bare_colon_script_convention(self):
        # The old design leaked instructions into speech via a "...pace: {script}"
        # convention. Ensure that fragile pattern is gone.
        prompt = ManusMediaClient._task_prompt(RequestKind.VOICE, "hi")
        self.assertNotIn("after the colon", prompt.lower())


class SingPromptTests(unittest.TestCase):
    def test_requires_real_instrumentation(self):
        prompt = ManusMediaClient._task_prompt(RequestKind.SING, "friendship jingle").lower()
        self.assertIn("instrument", prompt)
        self.assertIn("backing track", prompt)

    def test_forbids_spoken_or_a_cappella(self):
        prompt = ManusMediaClient._task_prompt(RequestKind.SING, "friendship jingle").lower()
        self.assertIn("a cappella", prompt)
        self.assertIn("must not be spoken", prompt)

    def test_user_brief_is_included(self):
        prompt = ManusMediaClient._task_prompt(RequestKind.SING, "upbeat Banglish jingle")
        self.assertIn("upbeat Banglish jingle", prompt)


if __name__ == "__main__":
    unittest.main()
