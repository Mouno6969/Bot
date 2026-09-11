import random
import re
import tempfile
import unittest
from pathlib import Path

from messenger_bot.games import (
    DAILY_CREDITS,
    DAILY_INTERVAL_SECONDS,
    FLIP_MIN_BET,
    GUESS_REWARD,
    QUIZ_REWARD,
    RACE_LANES,
    RACE_MIN_ENTRANTS,
    RACE_REWARD_PER_ENTRANT,
    STARTER_CREDITS,
    Edit,
    GameManager,
    Post,
)
from messenger_bot.questions import QUESTION_BANK, pick_question


TEXTS = lambda actions: [a.text for a in actions if isinstance(a, Post)]  # noqa: E731
EDITS = lambda actions: [a for a in actions if isinstance(a, Edit)]  # noqa: E731


class GameTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "bot_games.json"
        # Fixed seed: deterministic races, flips, and question picks per test.
        self.rng = random.Random(1234)
        self.games = GameManager(
            self.path,
            quiz_seconds=100,
            guess_seconds=100,
            race_lobby_seconds=50,
            race_frame_seconds=5,
            rng=self.rng,
        )
        self.now = 10_000.0

    def handle(self, kind, sender="Mouno", argument="", now=None):
        return self.games.handle(kind, sender, argument, self.now if now is None else now)


class EconomyTests(GameTestCase):
    def test_new_player_gets_starter_credits_on_balance(self):
        posts = TEXTS(self.handle("balance", sender="Rahim"))
        self.assertIn(f"Credits: {STARTER_CREDITS}", posts[0])
        self.assertIn("Leaderboard rank: #1", posts[0])

    def test_daily_grants_once_per_interval(self):
        posts = TEXTS(self.handle("daily", sender="Rahim"))
        self.assertIn(f"+{DAILY_CREDITS}", posts[0])
        player = self.games._player("Rahim")
        self.assertEqual(player.credits, STARTER_CREDITS + DAILY_CREDITS)
        # Second claim within the interval is refused and balance is unchanged.
        posts = TEXTS(self.handle("daily", sender="Rahim", now=self.now + 60))
        self.assertIn("abar pabe", posts[0])
        self.assertEqual(player.credits, STARTER_CREDITS + DAILY_CREDITS)
        # After the interval the bonus is granted again.
        posts = TEXTS(self.handle("daily", sender="Rahim", now=self.now + DAILY_INTERVAL_SECONDS + 1))
        self.assertIn(f"+{DAILY_CREDITS}", posts[0])

    def test_player_names_are_case_insensitive(self):
        self.handle("daily", sender="Karim Uddin")
        later = TEXTS(self.handle("balance", sender="KARIM uddin "))[0]
        self.assertIn(f"Credits: {STARTER_CREDITS + DAILY_CREDITS}", later)

    def test_ranking_orders_by_credits(self):
        self.handle("daily", sender="Top Scorer")
        self.handle("balance", sender="New Player")
        posts = TEXTS(self.handle("ranking"))[0]
        self.assertLess(posts.index("Top Scorer"), posts.index("New Player"))
        self.assertIn("credits", posts)

    def test_state_persists_across_manager_instances(self):
        self.handle("daily", sender="Rahim")
        reloaded = GameManager(self.path, rng=random.Random(1))
        player = reloaded._player("Rahim")
        self.assertEqual(player.credits, STARTER_CREDITS + DAILY_CREDITS)


class QuizTests(GameTestCase):
    def test_quiz_start_and_correct_answer_awards_credits(self):
        posts = TEXTS(self.handle("quiz", sender="Rahim"))
        self.assertIn("QUIZ TIME!", posts[0])
        self.assertIsNotNone(self.games.quiz)
        # Choosing anything but the correct letter loses nothing.
        correct_letter = "ABCD"[self.games.quiz.question.answer_index]
        wrong_letter = next(letter for letter in "ABCD" if letter != correct_letter)
        posts = TEXTS(self.handle("answer", sender="Kuddus", argument=wrong_letter))
        self.assertIn("Kuddus", posts[0])
        rewards_before = self.games._player("Kuddus").credits
        self.assertEqual(rewards_before, STARTER_CREDITS)
        # First correct answer wins and closes the round.
        posts = TEXTS(self.handle("answer", sender="Kuddus", argument=correct_letter))
        self.assertIn("SHOTHIK JAWAB", posts[0])
        self.assertIsNone(self.games.quiz)
        winner = self.games._player("Kuddus")
        self.assertEqual(winner.credits, STARTER_CREDITS + QUIZ_REWARD)
        self.assertEqual(winner.wins, 1)

    def test_numeric_answers_are_accepted(self):
        self.handle("quiz", sender="Rahim")
        correct_number = str(self.games.quiz.question.answer_index + 1)
        self.handle("answer", sender="Rahim", argument=correct_number)
        self.assertEqual(self.games._player("Rahim").credits, STARTER_CREDITS + QUIZ_REWARD)

    def test_answer_without_quiz_explains(self):
        posts = TEXTS(self.handle("answer", sender="Rahim", argument="A"))
        self.assertIn("kono quiz choltese na", posts[0])

    def test_second_quiz_blocked_while_active(self):
        self.handle("quiz", sender="Rahim")
        posts = TEXTS(self.handle("quiz", sender="Karim", now=self.now + 1))
        self.assertIn("already choltese", posts[0])

    def test_quiz_expiry_reveals_answer_in_tick(self):
        self.handle("quiz", sender="Rahim")
        actions = self.games.tick(self.now + 1000)
        posts = TEXTS(actions)
        self.assertIn("shomoy sesh", posts[0])
        self.assertIsNone(self.games.quiz)

    def test_quiz_cooldown_after_resolve(self):
        self.handle("quiz", sender="Rahim")
        correct_letter = "ABCD"[self.games.quiz.question.answer_index]
        self.handle("answer", sender="Rahim", argument=correct_letter, now=self.now + 5)
        posts = TEXTS(self.handle("quiz", sender="Karim", now=self.now + 10))
        self.assertIn("Quiz ektu agei shesh", posts[0])

    def test_unknown_category_is_rejected_with_options(self):
        posts = TEXTS(self.handle("quiz", sender="Rahim", argument="quantum"))
        self.assertIn("category nei", posts[0])
        self.assertIsNone(self.games.quiz)

    def test_category_filter_selects_matching_questions(self):
        t = self.now
        for _ in range(3):
            posts = TEXTS(self.handle("quiz", sender="Rahim", argument="sports", now=t))
            self.assertIn("category: sports", posts[0])
            correct_letter = "ABCD"[self.games.quiz.question.answer_index]
            self.handle("answer", sender="Rahim", argument=correct_letter, now=t + 10)
            t += 500  # pass the inter-quiz cooldown

    def test_question_picker_avoids_recent_repeats(self):
        used: list[int] = []
        rng = random.Random(7)
        seen = []
        for _ in range(12):
            index, _question = pick_question(rng, used)
            seen.append(index)
        # No repeats inside the retry window when the bank is large enough.
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(QUESTION_BANK) > 12, True)


class FlipTests(GameTestCase):
    def test_flip_changes_balance_exactly_by_bet(self):
        posts = TEXTS(self.handle("flip", sender="Rahim", argument=f"heads {FLIP_MIN_BET}"))
        player = self.games._player("Rahim")
        if "Jitli" in posts[0]:
            self.assertEqual(player.credits, STARTER_CREDITS + FLIP_MIN_BET)
        else:
            self.assertEqual(player.credits, STARTER_CREDITS - FLIP_MIN_BET)

    def test_flip_rejects_bad_input_and_overspending(self):
        self.assertIn("Format:", TEXTS(self.handle("flip", sender="Rahim", argument="heads"))[0])
        self.assertIn("Format:", TEXTS(self.handle("flip", sender="Rahim", argument="maybe 50"))[0])
        posts = TEXTS(self.handle("flip", sender="Rahim", argument="tails 99999"))
        self.assertIn("tomar kache", posts[0])
        self.assertEqual(self.games._player("Rahim").credits, STARTER_CREDITS)


class GuessTests(GameTestCase):
    def test_guess_round_full_flow(self):
        posts = TEXTS(self.handle("guess", sender="Rahim"))
        self.assertIn("NUMBER GUESS shuru", posts[0])
        target = self.games.guess.target
        posts = TEXTS(self.handle("guess", sender="Karim", argument=str(50 if target != 50 else 49)))
        self.assertTrue("boro" in posts[0] or "chhoto" in posts[0])
        posts = TEXTS(self.handle("guess", sender="Karim", argument=str(target)))
        self.assertIn("PERFECT", posts[0])
        player = self.games._player("Karim")
        self.assertEqual(player.credits, STARTER_CREDITS + GUESS_REWARD)
        self.assertIsNone(self.games.guess)

    def test_guess_without_round_and_expiry(self):
        posts = TEXTS(self.handle("guess", sender="Rahim", argument="25"))
        self.assertIn("kono guess round nei", posts[0])
        self.handle("guess", sender="Rahim")
        actions = self.games.tick(self.now + 1000)
        self.assertIn("Guess round shesh", TEXTS(actions)[0])
        self.assertIsNone(self.games.guess)


class RaceTests(GameTestCase):
    def open_lobby(self):
        posts = TEXTS(self.handle("race", sender="Rahim"))
        self.assertIn("LOBBY OPEN", posts[0])
        race = self.games.race
        self.assertIsNotNone(race)
        return race

    def test_lobby_open_join_and_go(self):
        race = self.open_lobby()
        self.assertEqual(race.entrants[1], "Rahim")
        posts = TEXTS(self.handle("race", sender="Karim", argument="3", now=self.now + 2))
        self.assertIn("lane 3", posts[0])
        posts = TEXTS(self.handle("race", sender="Karim", argument="3", now=self.now + 3))
        self.assertIn("already", posts[0])
        posts = TEXTS(self.handle("race", sender="Babul", argument="3", now=self.now + 4))
        self.assertIn("niye niyeche", posts[0])
        # Non-creator cannot start the race.
        posts = TEXTS(self.handle("race", sender="Karim", argument="go", now=self.now + 5))
        self.assertIn("sudhu uni", posts[0])
        # Creator starts it; frames are rendered with all lanes.
        actions = self.handle("race", sender="Rahim", argument="go", now=self.now + 6)
        posts = TEXTS(actions)
        self.assertIn("SHURU HOLLO", posts[0])
        self.assertIn("HORSE RACE #", posts[1])
        self.assertEqual(race.phase, "running")
        self.assertEqual(len(race.positions), RACE_LANES)

    def test_lobby_cancels_with_too_few_players(self):
        race = self.open_lobby()
        actions = self.games.tick(self.now + 100)
        self.assertIn("cancel", TEXTS(actions)[0])
        self.assertIsNone(self.games.race)
        # A cancelled lobby only gets a short cooldown, so a new race starts quickly.
        posts = TEXTS(self.handle("race", sender="Rahim", now=self.now + 111))
        self.assertIn("LOBBY OPEN", posts[0])

    def test_lobby_auto_starts_and_race_finishes_with_payout(self):
        race = self.open_lobby()
        self.handle("race", sender="Karim", argument="2", now=self.now + 2)
        entrants_before = dict(race.entrants)
        actions = self.games.tick(self.now + 60)
        self.assertIn("SHURU HOLLO", TEXTS(actions)[0])
        # Drive frames until the race resolves; each tick advances exactly one frame.
        frame_posts: list[str] = []
        for frame in range(1, 40):
            actions = self.games.tick(self.now + 60 + frame * 5)
            frame_posts.extend(TEXTS(actions))
            if self.games.race is None:
                break
            edits = EDITS(actions)
            self.assertTrue(edits, f"frame {frame} produced no edit")
            self.assertIn("HORSE RACE #", edits[0].anchor)
        self.assertIsNone(self.games.race, "race should finish within 40 frames")
        result = "\n".join(frame_posts)
        self.assertIn("RESULT", result)
        # If a player horse won, the pot was paid to its owner; an NPC win pays nobody.
        winner_match = re.search(r"WINNER: .*#(\d+)", result)
        if winner_match:
            owner = entrants_before.get(int(winner_match.group(1)))
            self.assertIsNotNone(owner)
            player = self.games._player(owner)
            self.assertGreaterEqual(
                player.credits, STARTER_CREDITS + RACE_MIN_ENTRANTS * RACE_REWARD_PER_ENTRANT
            )
            self.assertIn("Pot:", result)
        else:
            self.assertIn("NPC", result)

    def test_race_cooldown_after_finish(self):
        race = self.open_lobby()
        self.handle("race", sender="Karim", argument="2", now=self.now + 2)
        self.games.tick(self.now + 60)
        for frame in range(1, 40):
            self.games.tick(self.now + 60 + frame * 5)
            if self.games.race is None:
                break
        posts = TEXTS(self.handle("race", sender="Rahim", now=self.now + 300))
        self.assertTrue("por abar" in posts[0] or "LOBBY OPEN" in posts[0])

    def test_join_during_running_race_is_rejected(self):
        self.open_lobby()
        self.handle("race", sender="Karim", argument="2", now=self.now + 2)
        self.handle("race", sender="Rahim", argument="go", now=self.now + 3)
        posts = TEXTS(self.handle("race", sender="Babul", now=self.now + 4))
        self.assertIn("choltese", posts[0])


class RobustnessTests(GameTestCase):
    def test_unknown_kind_gets_friendly_message(self):
        posts = TEXTS(self.handle("not-a-game", sender="Rahim"))
        self.assertIn("/games", posts[0])

    def test_tick_is_empty_when_idle(self):
        self.assertEqual(self.games.tick(self.now), [])


if __name__ == "__main__":
    unittest.main()
