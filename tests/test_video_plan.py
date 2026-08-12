import unittest

from messenger_bot.video_plan import (
    ALLOWED_MOODS,
    MAX_SCENES,
    MIN_SCENES,
    VideoPlan,
    build_planner_prompt,
    parse_plan,
)


class BuildPlannerPromptTests(unittest.TestCase):
    def test_video_prompt_asks_for_spoken_voiceover(self):
        prompt = build_planner_prompt("shubho sokal", is_music=False)
        self.assertIn("VOICEOVER", prompt)
        self.assertIn("shubho sokal", prompt)
        for mood in ALLOWED_MOODS:
            self.assertIn(mood, prompt)

    def test_music_prompt_asks_for_a_song(self):
        prompt = build_planner_prompt("bondhutter gaan", is_music=True)
        self.assertIn("song", prompt.lower())
        self.assertIn("lyric", prompt.lower())


class ParsePlanTests(unittest.TestCase):
    def test_clean_json_is_parsed(self):
        raw = (
            '{"idea": "friends at sunset", "scenes": ["a beach at dusk", "two friends laughing"],'
            ' "script": "Bondhura mile shomoy kata.", "mood": "calm"}'
        )
        plan = parse_plan(raw, "friendship", is_music=False)
        self.assertEqual(plan.idea, "friends at sunset")
        self.assertEqual(plan.scenes, ["a beach at dusk", "two friends laughing"])
        self.assertEqual(plan.script, "Bondhura mile shomoy kata.")
        self.assertEqual(plan.mood, "calm")

    def test_fenced_json_with_prose_is_tolerated(self):
        raw = (
            "Sure! Here is your plan:\n```json\n"
            '{"idea":"x","scenes":["s1","s2","s3"],"script":"hi","mood":"energetic"}\n```\nEnjoy!'
        )
        plan = parse_plan(raw, "party", is_music=True)
        self.assertEqual(plan.scenes, ["s1", "s2", "s3"])
        self.assertEqual(plan.mood, "energetic")

    def test_too_many_scenes_are_clamped(self):
        raw = '{"scenes": ["1","2","3","4","5"], "script": "x", "mood": "calm"}'
        plan = parse_plan(raw, "p", is_music=False)
        self.assertEqual(len(plan.scenes), MAX_SCENES)

    def test_single_scene_is_padded_to_minimum(self):
        raw = '{"scenes": ["only one"], "script": "x", "mood": "calm"}'
        plan = parse_plan(raw, "p", is_music=False)
        self.assertEqual(len(plan.scenes), MIN_SCENES)
        self.assertEqual(plan.scenes[0], "only one")

    def test_missing_scenes_fall_back_to_user_prompt(self):
        plan = parse_plan('{"script": "hi", "mood": "calm"}', "a rainy day", is_music=False)
        self.assertEqual(len(plan.scenes), MIN_SCENES)
        self.assertTrue(all("a rainy day" in s for s in plan.scenes))

    def test_missing_script_falls_back_to_user_prompt(self):
        plan = parse_plan('{"scenes":["a","b"], "mood":"calm"}', "say something nice", is_music=False)
        self.assertEqual(plan.script, "say something nice")

    def test_invalid_mood_defaults_by_command(self):
        video = parse_plan('{"scenes":["a","b"],"script":"x","mood":"banana"}', "p", is_music=False)
        music = parse_plan('{"scenes":["a","b"],"script":"x","mood":"banana"}', "p", is_music=True)
        self.assertEqual(video.mood, "cinematic")
        self.assertEqual(music.mood, "energetic")

    def test_total_garbage_still_yields_a_valid_plan(self):
        plan = parse_plan("the model refused and wrote only prose", "my prompt", is_music=False)
        self.assertIsInstance(plan, VideoPlan)
        self.assertGreaterEqual(len(plan.scenes), MIN_SCENES)
        self.assertTrue(plan.script)
        self.assertIn(plan.mood, ALLOWED_MOODS)

    def test_empty_reply_yields_a_valid_plan(self):
        plan = parse_plan("", "fallback prompt", is_music=True)
        self.assertEqual(plan.idea, "fallback prompt")
        self.assertEqual(plan.mood, "energetic")


if __name__ == "__main__":
    unittest.main()
