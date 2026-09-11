"""Credits, quiz, horse-race, number-guess, coin-flip, and leaderboard games.

All game state is group-local. Credits and win counts persist in a small JSON
file (never committed, alongside bot_state.json), while an active round lives
only in memory so a bot restart never leaves a race half-finished — stale
rounds simply expire.

The module is UI-agnostic on purpose: command handlers and the periodic
``tick()`` return a list of actions (``Post`` a message, or ``Edit`` the bot's
newest message matching an anchor). The Messenger layer executes them, which
keeps every rule here unit-testable without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import random
from typing import Any

from .questions import QUESTION_BANK, Question, available_categories, pick_question


# --- economy tuning ------------------------------------------------------------
STARTER_CREDITS = 100          # one-time grant the first time a player interacts
DAILY_CREDITS = 60             # /daily bonus
DAILY_INTERVAL_SECONDS = 20 * 3600
QUIZ_REWARD = 40
GUESS_REWARD = 30
GUESS_MAX_NUMBER = 50
RACE_REWARD_PER_ENTRANT = 20   # pot = entrants x this
FLIP_MIN_BET = 10

# --- pacing --------------------------------------------------------------------
QUIZ_SECONDS = 120.0           # answer window before the question is revealed
QUIZ_COOLDOWN_SECONDS = 30.0   # breathing room after a quiz resolves
GUESS_SECONDS = 180.0
RACE_LOBBY_SECONDS = 45.0
RACE_FRAME_SECONDS = 4.0
RACE_COOLDOWN_SECONDS = 60.0
RACE_CANCEL_COOLDOWN_SECONDS = 10.0  # cancelled lobbies recycle much faster
RACE_LANES = 6
RACE_TRACK_LENGTH = 12         # frames are text art; the lane is this many cells wide
RACE_MIN_ENTRANTS = 2


# --- actions returned to the caller ---------------------------------------------
@dataclass(frozen=True)
class Post:
    """A text message the caller should send as a new group message."""

    text: str


@dataclass(frozen=True)
class Edit:
    """Replace the bot's newest group message whose text contains ``anchor``.

    Messenger has no bot edit API, so the browser layer re-locates the message
    by this anchor text. Keep anchors short, unique, and emoji-free.
    """

    anchor: str
    text: str


Action = Post | Edit


# --- players and persistence ------------------------------------------------------
def _player_key(name: str) -> str:
    return " ".join((name or "").split()).casefold() or "player"


@dataclass
class Player:
    name: str
    credits: int = STARTER_CREDITS
    wins: int = 0
    plays: int = 0
    last_daily: float = 0.0
    best_game: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "credits": self.credits,
            "wins": self.wins,
            "plays": self.plays,
            "last_daily": self.last_daily,
            "best_game": self.best_game,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Player":
        return cls(
            name=str(raw.get("name", "Player")),
            credits=int(raw.get("credits", STARTER_CREDITS)),
            wins=int(raw.get("wins", 0)),
            plays=int(raw.get("plays", 0)),
            last_daily=float(raw.get("last_daily", 0.0)),
            best_game=str(raw.get("best_game", "")),
        )


# --- active round state (in-memory only) ------------------------------------------
@dataclass
class QuizRound:
    question: Question
    asked_by: str
    asked_at: float
    wrong_attempts: int = 0


@dataclass
class GuessRound:
    target: int
    started_by: str
    started_at: float
    attempts: int = 0


@dataclass
class RaceRound:
    race_id: int
    creator: str
    entrants: dict[int, str]  # lane number (1-based) -> player display name
    opened_at: float
    phase: str = "lobby"  # "lobby" | "running"
    positions: list[int] = field(default_factory=lambda: [0] * RACE_LANES)
    frame_no: int = 0
    last_frame_at: float = 0.0

    @property
    def anchor(self) -> str:
        return f"HORSE RACE #{self.race_id}"


_LETTERS = "ABCD"
_ANSWER_TO_INDEX = {letter: index for index, letter in enumerate(_LETTERS)}
_ANSWER_TO_INDEX.update({str(index + 1): index for index in range(len(_LETTERS))})
_HORSE = "🐎"
_FINISH = "🏁"
_TRACK_CELL = "-"


class GameManager:
    """Stateful game engine; all methods are synchronous and exception-free."""

    def __init__(
        self,
        path: Path,
        *,
        quiz_seconds: float = QUIZ_SECONDS,
        guess_seconds: float = GUESS_SECONDS,
        race_lobby_seconds: float = RACE_LOBBY_SECONDS,
        race_frame_seconds: float = RACE_FRAME_SECONDS,
        rng: random.Random | None = None,
    ) -> None:
        self.path = path
        self.quiz_seconds = quiz_seconds
        self.guess_seconds = guess_seconds
        self.race_lobby_seconds = race_lobby_seconds
        self.race_frame_seconds = race_frame_seconds
        self.rng = rng or random.Random()
        self.players: dict[str, Player] = {}
        self.stats = {"races": 0, "quizzes": 0, "guesses": 0, "flips": 0}
        self.used_questions: list[int] = []
        self.quiz: QuizRound | None = None
        self.guess: GuessRound | None = None
        self.race: RaceRound | None = None
        self.last_quiz_resolved_at = 0.0
        self.last_race_finished_at = 0.0
        self._load()

    # -- persistence -------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.players = {
                _player_key(item.get("name", "")): Player.from_dict(item)
                for item in raw.get("players", [])
            }
            self.stats.update(raw.get("stats", {}))
            self.used_questions = [int(i) for i in raw.get("used_questions", [])][-len(QUESTION_BANK):]
        except (FileNotFoundError, OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self) -> None:
        try:
            self.path.write_text(
                json.dumps(
                    {
                        "players": [p.to_dict() for p in self.players.values()],
                        "stats": self.stats,
                        "used_questions": self.used_questions[-len(QUESTION_BANK):],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as error:
            print(f"Could not save game state: {error}")

    # -- helpers --------------------------------------------------------------------
    def _player(self, sender: str) -> Player:
        key = _player_key(sender)
        player = self.players.get(key)
        if player is None:
            player = Player(name=(" ".join((sender or "").split())) or "Player")
            self.players[key] = player
            self.save()
        return player

    def _rank_of(self, key: str) -> int:
        ordered = sorted(
            self.players.items(), key=lambda item: (-item[1].credits, -item[1].wins, item[1].name)
        )
        for index, (player_key, _) in enumerate(ordered, start=1):
            if player_key == key:
                return index
        return len(ordered) + 1

    @staticmethod
    def _hourglass(seconds_left: float) -> str:
        return f"⏰ {int(seconds_left)}s baki"

    # -- command dispatch --------------------------------------------------------------
    def handle(self, kind: str, sender: str, argument: str, now: float) -> list[Action]:
        """Run one game command; returns messages to post back to the group."""
        try:
            handler = {
                "balance": self._cmd_balance,
                "daily": self._cmd_daily,
                "ranking": self._cmd_ranking,
                "quiz": self._cmd_quiz,
                "answer": self._cmd_answer,
                "race": self._cmd_race,
                "guess": self._cmd_guess,
                "flip": self._cmd_flip,
            }[kind]
        except KeyError:
            return [Post("Oi game command ta chini na. `/games` likhle shob list pabe.")]
        try:
            return handler(sender, argument, now)
        except Exception as error:  # noqa: BLE001 — a game must never crash the monitor
            print(f"Game command {kind} failed: {error}")
            return [Post("Eitar moddhe ekta problem hoyeche; abar try koro.")]

    # -- economy commands ------------------------------------------------------------
    def _cmd_balance(self, sender: str, _argument: str, _now: float) -> list[Action]:
        player = self._player(sender)
        rank = self._rank_of(_player_key(sender))
        return [
            Post(
                f"💰 {player.name}-er wallet\n"
                f"Credits: {player.credits}\n"
                f"Games khela hoyeche: {player.plays} | Jit: {player.wins}\n"
                f"Leaderboard rank: #{rank}"
            )
        ]

    def _cmd_daily(self, sender: str, _argument: str, now: float) -> list[Action]:
        player = self._player(sender)
        elapsed = now - player.last_daily
        if player.last_daily and elapsed < DAILY_INTERVAL_SECONDS:
            hours_left = (DAILY_INTERVAL_SECONDS - elapsed) / 3600
            return [
                Post(
                    f"🕐 {player.name}, daily bonus ekbar e niyechho! "
                    f"Aro {hours_left:.1f} ghonta por abar pabe."
                )
            ]
        player.last_daily = now
        player.credits += DAILY_CREDITS
        self.save()
        return [
            Post(
                f"🎁 Daily bonus! {player.name} pailo +{DAILY_CREDITS} credits. "
                f"New balance: {player.credits} 💰"
            )
        ]

    def _cmd_ranking(self, _sender: str, _argument: str, _now: float) -> list[Action]:
        if not self.players:
            return [Post("Ekhono keu kono game khele ni! `/quiz`, `/race` ba `/guess` diye shuru koro.")]
        ordered = sorted(
            self.players.values(), key=lambda p: (-p.credits, -p.wins, p.name)
        )[:10]
        lines = ["🏆 CREDIT LEADERBOARD 🏆", ""]
        medals = ["🥇", "🥈", "🥉"]
        for index, player in enumerate(ordered, start=1):
            medal = medals[index - 1] if index <= len(medals) else f"{index}."
            lines.append(f"{medal} {player.name} — {player.credits} credits ({player.wins} wins)")
        total_games = self.stats["quizzes"] + self.stats["races"] + self.stats["guesses"] + self.stats["flips"]
        lines.append("")
        lines.append(
            f"📊 Total: {total_games} games "
            f"(quiz {self.stats['quizzes']} | race {self.stats['races']} | guess {self.stats['guesses']} | flip {self.stats['flips']})"
        )
        return [Post("\n".join(lines))]

    # -- quiz -------------------------------------------------------------------------
    def _cmd_quiz(self, sender: str, argument: str, now: float) -> list[Action]:
        player = self._player(sender)
        if self.quiz is not None:
            left = self.quiz_seconds - (now - self.quiz.asked_at)
            return [
                Post(
                    f"Ekta quiz already choltese! ({self._hourglass(max(0.0, left))}) "
                    "`/answer A/B/C/D` diye jawab dao."
                )
            ]
        if now - self.last_quiz_resolved_at < QUIZ_COOLDOWN_SECONDS:
            wait = int(QUIZ_COOLDOWN_SECONDS - (now - self.last_quiz_resolved_at))
            return [Post(f"Quiz ektu agei shesh hoyeche — {wait}s por abar `/quiz` dao. 😌")]
        category = " ".join((argument or "").split())
        categories = available_categories()
        if category and category.casefold() not in [c.casefold() for c in categories]:
            return [
                Post(
                    f"'{category}' category nei. Options: {', '.join(categories)} — "
                    "ba khali `/quiz` dao jekono category theke."
                )
            ]
        index, question = pick_question(self.rng, self.used_questions, category)
        self.quiz = QuizRound(question=question, asked_by=player.name, asked_at=now)
        self.stats["quizzes"] += 1
        player.plays += 1
        self.save()
        option_lines = "\n".join(
            f"{_LETTERS[i]}) {option}" for i, option in enumerate(question.options)
        )
        return [
            Post(
                f"🎯 QUIZ TIME! (category: {question.category})\n\n"
                f"{question.prompt}\n\n"
                f"{option_lines}\n\n"
                f"Amake mention kore `/answer A` / `/answer B` likhe jawab dao — prothom shothik jawab "
                f"pabe 🎁 {QUIZ_REWARD} credits! ⏰ {int(self.quiz_seconds)}s\n"
                f"(Question #{index + 1}, started by {player.name})"
            )
        ]

    def _cmd_answer(self, sender: str, argument: str, now: float) -> list[Action]:
        player = self._player(sender)
        if self.quiz is None:
            return [Post("Ekhon kono quiz choltese na. `/quiz` diye notun ekta shuru koro!")]
        quiz = self.quiz
        if now - quiz.asked_at > self.quiz_seconds:
            return [Post("Ei quiz er shomoy sesh! Answer reveal hobe ekkhuni — notun quiz dao `/quiz`.")]
        token = " ".join((argument or "").split()).strip(".:").upper()
        if token not in _ANSWER_TO_INDEX:
            return [
                Post(
                    f"{player.name}, jawab ekta letter diye dao: `/answer A`, `/answer B`, `/answer C` ba `/answer D`."
                )
            ]
        player.plays += 1
        if _ANSWER_TO_INDEX[token] != quiz.question.answer_index:
            quiz.wrong_attempts += 1
            self.save()
            cheers = [
                "Bhul jawab! 😅 Abar try koro.",
                "Nah, eta thik na! ❌ Aro chinta koro.",
                "Miss! 🙈 Keo nishchit e giye jawab dibe...",
            ]
            return [Post(f"{player.name}: {self.rng.choice(cheers)}")]
        correct = quiz.question.options[quiz.question.answer_index]
        reward = QUIZ_REWARD
        player.wins += 1
        player.credits += reward
        self.quiz = None
        self.last_quiz_resolved_at = now
        self.save()
        fact_line = f"\n💡 {quiz.question.fact}" if quiz.question.fact else ""
        return [
            Post(
                f"🎉 SHOTHIK JAWAB! Winner: {player.name} 🏆\n"
                f"Answer chhilo {_LETTERS[quiz.question.answer_index]}) {correct}.{fact_line}\n"
                f"+{reward} credits! Balance: {player.credits} 💰 "
                f"(wrong attempts: {quiz.wrong_attempts})"
            )
        ]

    # -- coin flip ----------------------------------------------------------------------
    def _cmd_flip(self, sender: str, argument: str, _now: float) -> list[Action]:
        player = self._player(sender)
        parts = " ".join((argument or "").split()).split()
        side = parts[0].casefold() if parts else ""
        if side in ("h", "head"):
            side = "heads"
        elif side in ("t", "tail"):
            side = "tails"
        amount = 0
        if len(parts) >= 2 and parts[1].isdigit():
            amount = int(parts[1])
        if side not in ("heads", "tails") or amount < FLIP_MIN_BET:
            return [
                Post(
                    f"Format: `/flip heads 50` ba `/flip tails 50`. "
                    f"Minimum bet {FLIP_MIN_BET} credits. Tomar balance: {player.credits} 💰"
                )
            ]
        if amount > player.credits:
            return [
                Post(
                    f"{player.name}, tomar kache {amount} credits nei! "
                    f"Balance: {player.credits}. `/daily` diye free credits nao. 🙃"
                )
            ]
        result = self.rng.choice(("heads", "tails"))
        player.plays += 1
        self.stats["flips"] += 1
        if result == side:
            player.wins += 1
            player.credits += amount
            outcome = f"🎉 Coin: *{result}*! Jitli {player.name}! +{amount} credits."
        else:
            player.credits -= amount
            outcome = f"💸 Coin: *{result}* — haare gela {player.name}. -{amount} credits."
        self.save()
        return [Post(f"🪙 FLIP! {outcome} New balance: {player.credits} 💰")]

    # -- number guess ------------------------------------------------------------------
    def _cmd_guess(self, sender: str, argument: str, now: float) -> list[Action]:
        player = self._player(sender)
        token = " ".join((argument or "").split())
        if not token:
            # No argument -> start a new round.
            if self.guess is not None:
                left = self.guess_seconds - (now - self.guess.started_at)
                return [
                    Post(
                        f"Ekta guess round already choltese! ({self._hourglass(max(0.0, left))}) "
                        f"`/guess 25` er moto kore number dao."
                    )
                ]
            self.guess = GuessRound(
                target=self.rng.randint(1, GUESS_MAX_NUMBER), started_by=player.name, started_at=now
            )
            self.stats["guesses"] += 1
            player.plays += 1
            self.save()
            return [
                Post(
                    f"🎲 NUMBER GUESS shuru! Ami 1-{GUESS_MAX_NUMBER} er moddhe ekta number bhebechi.\n"
                    f"Amake mention kore `/guess <number>` likhe try koro — shothik guess pabe "
                    f"🎁 {GUESS_REWARD} credits! ⏰ {int(self.guess_seconds)}s "
                    f"(started by {player.name})"
                )
            ]
        if self.guess is None:
            return [Post("Ekhon kono guess round nei. Khali `/guess` dao, ami number bhabbo! 🎲")]
        if not token.lstrip("-").isdigit():
            return [Post(f"{player.name}, shudhu number dao — jemon `/guess 27`.")]
        guess = self.guess
        if now - guess.started_at > self.guess_seconds:
            return [Post("Ei round er shomoy sesh! Notun round e `/guess` dao.")]
        value = int(token)
        if not 1 <= value <= GUESS_MAX_NUMBER:
            return [Post(f"{player.name}, 1 theke {GUESS_MAX_NUMBER} er moddhe guess dao! 😄")]
        guess.attempts += 1
        player.plays += 1
        if value == guess.target:
            player.wins += 1
            player.credits += GUESS_REWARD
            self.guess = None
            self.save()
            return [
                Post(
                    f"🎉 PERFECT! {player.name} thik kore bolse — number chhilo {value}!\n"
                    f"+{GUESS_REWARD} credits 🎁 Balance: {player.credits} 💰 "
                    f"(mot {guess.attempts} ta guess e)"
                )
            ]
        self.save()
        hint = "boro (higher) ⬆️" if value < guess.target else "chhoto (lower) ⬇️"
        return [Post(f"{player.name}: {value} na! Amar number ta aro {hint}")]

    # -- horse race ----------------------------------------------------------------------
    def _cmd_race(self, sender: str, argument: str, now: float) -> list[Action]:
        player = self._player(sender)
        race = self.race
        if race is None:
            if now - self.last_race_finished_at < RACE_COOLDOWN_SECONDS:
                wait = int(RACE_COOLDOWN_SECONDS - (now - self.last_race_finished_at))
                return [Post(f"Race ektu agei shesh hoyeche 🏇 — {wait}s por abar `/race` dao.")]
            self.stats["races"] += 1
            self.save()
            race = RaceRound(
                race_id=self.stats["races"], creator=player.name, entrants={}, opened_at=now
            )
            race.entrants[self._claim_lane(race, None)] = player.name
            self.race = race
            player.plays += 1
            self.save()
            return [Post(self._lobby_text(race, opened_by=player.name))]
        if race.phase == "running":
            return [Post(f"{race.anchor} ekkhuni choltese! 🏇 Shesh hobar por notun race `/race` dao.")]
        token = " ".join((argument or "").split()).casefold()
        if token in ("start", "go"):
            if _player_key(player.name) != _player_key(race.creator):
                return [Post(f"Lobby ta {race.creator} khuleche — sudhu uni `/race go` dite pare. 🙂")]
            if len(race.entrants) < RACE_MIN_ENTRANTS:
                return [
                    Post(
                        f"Aro {RACE_MIN_ENTRANTS - len(race.entrants)} jon join chaia minimum! "
                        "Bhaigulo ke bolo `/race` dite. 🏇"
                    )
                ]
            return self._start_race(race, now)
        requested: int | None = None
        if token:
            if not token.isdigit() or not 1 <= int(token) <= RACE_LANES:
                return [Post(f"Lane number 1-{RACE_LANES} er moddhe dao — jemon `/race 3`.")]
            requested = int(token)
        if _player_key(player.name) in [_player_key(name) for name in race.entrants.values()]:
            lane = [num for num, name in race.entrants.items() if _player_key(name) == _player_key(player.name)][0]
            return [Post(f"{player.name}, tumi already lane {lane} e acho! 😄 `/race go` er opekkha koro.")]
        if requested is not None and requested in race.entrants:
            return [Post(f"Lane {requested} to {race.entrants[requested]} niye niyeche! Onno lane dao.")]
        if len(race.entrants) >= RACE_LANES:
            return [Post("Shob lane full! Ektu darao, race shuru hobe kichhukkhoner moddhei. 🏇")]
        lane = self._claim_lane(race, requested)
        race.entrants[lane] = player.name
        player.plays += 1
        self.save()
        return [
            Post(
                f"✅ {player.name} join korlo lane {lane}! ({len(race.entrants)}/{RACE_LANES})\n"
                f"{int(max(0.0, self.race_lobby_seconds - (now - race.opened_at)))}s por race auto-start, "
                f"ba {race.creator} `/race go` dite pare."
            )
        ]

    def _claim_lane(self, race: RaceRound, requested: int | None) -> int:
        if requested is not None and requested not in race.entrants:
            return requested
        for lane in range(1, RACE_LANES + 1):
            if lane not in race.entrants:
                return lane
        raise RuntimeError("no free lane")

    def _lobby_text(self, race: RaceRound, opened_by: str) -> str:
        lanes = "\n".join(
            f"  Lane {lane}: {race.entrants.get(lane, '· (khali — NPC hobe)')}"
            for lane in range(1, RACE_LANES + 1)
        )
        return (
            f"🐎 {race.anchor} — LOBBY OPEN! 🐎\n\n"
            f"{lanes}\n\n"
            f"Join korte amake mention kore `/race` (auto lane) ba `/race 3` (specific lane) dao. "
            f"🎁 Winner pabe pot: players x {RACE_REWARD_PER_ENTRANT} credits!\n"
            f"⏰ {int(self.race_lobby_seconds)}s por auto-start (minimum {RACE_MIN_ENTRANTS} jon laga). "
            f"{opened_by} chaile `/race go` dite pare."
        )

    def _start_race(self, race: RaceRound, now: float) -> list[Action]:
        race.phase = "running"
        race.last_frame_at = now
        race.frame_no = 0
        entrants_line = ", ".join(
            f"#{lane} {self._owner_label(race, lane)}" for lane in sorted(race.entrants)
        )
        return [
            Post(
                f"🚦 {race.anchor} SHURU HOLLO! 🚦\n"
                f"Jatra: {entrants_line}\n"
                f"Baki lane gulo NPC chalabe. Race track ektu porei asche... 🏇💨"
            ),
            Post(self._frame_text(race)),
        ]

    def _owner_label(self, race: RaceRound, lane: int) -> str:
        return race.entrants.get(lane, "NPC")

    def _lane_line(self, race: RaceRound, lane: int) -> str:
        position = min(race.positions[lane - 1], RACE_TRACK_LENGTH)
        track = (
            _TRACK_CELL * position + _HORSE + _TRACK_CELL * (RACE_TRACK_LENGTH - position) + _FINISH
        )
        owner = self._owner_label(race, lane)
        return f"{lane}|{track}| {owner}"

    def _frame_text(self, race: RaceRound, events: list[str] | None = None) -> str:
        lines = [
            f"{_FINISH} {race.anchor} {_FINISH} (frame {race.frame_no})",
            "",
            *(self._lane_line(race, lane) for lane in range(1, RACE_LANES + 1)),
            "",
        ]
        if events:
            lines.append(" ".join(events))
        lines.append("Race choltese... 🏇💨")
        return "\n".join(lines)

    def _advance_race(self, race: RaceRound, now: float) -> list[Action]:
        race.frame_no += 1
        race.last_frame_at = now
        events: list[str] = []
        finished: list[int] = []
        for index in range(RACE_LANES):
            if race.positions[index] >= RACE_TRACK_LENGTH:
                continue
            roll = self.rng.random()
            if roll < 0.10:
                step = 0
                events.append(f"💨 #{index + 1} stumble!")
            elif roll > 0.90:
                step = 3
                events.append(f"⚡ #{index + 1} turbo!")
            else:
                step = self.rng.choice((0, 1, 1, 2))
            race.positions[index] += step
        for index in range(RACE_LANES):
            if race.positions[index] >= RACE_TRACK_LENGTH:
                finished.append(index + 1)
        if not finished:
            return [Edit(race.anchor, self._frame_text(race, events))]
        return self._finish_race(race, events, now)

    def _finish_race(self, race: RaceRound, events: list[str], now: float) -> list[Action]:
        # First lane to reach/past the line wins; breaks random ties.
        best = max(race.positions)
        leaders = [lane for lane in range(1, RACE_LANES + 1) if race.positions[lane - 1] == best]
        winner_lane = self.rng.choice(leaders)
        self.race = None
        self.last_race_finished_at = now
        final_lines = list(self._frame_text(race, events).splitlines())
        final_lines[-1] = f"🏆 {_HORSE} #{winner_lane} wins! Race sesh! {_FINISH}"
        final_frame = "\n".join(final_lines)
        actions: list[Action] = [Edit(race.anchor, final_frame)]
        owner = race.entrants.get(winner_lane)
        if owner is None:
            actions.append(
                Post(
                    f"🏆 {race.anchor} RESULT\n"
                    f"Jita gelo {_HORSE} #{winner_lane} — NPC! 😅 Player horses haare, keu pot paini.\n"
                    f"Abar ekdom jomjomat ekta race dao — `/race`!"
                )
            )
            return actions
        player = self._player(owner)
        pot = len(race.entrants) * RACE_REWARD_PER_ENTRANT
        player.wins += 1
        player.credits += pot
        player.best_game = "race"
        self.save()
        actions.append(
            Post(
                f"🏆 {race.anchor} RESULT 🏆\n"
                f"WINNER: {_HORSE} #{winner_lane} — {player.name}! 🎉🎉\n"
                f"Pot: +{pot} credits ({len(race.entrants)} players x {RACE_REWARD_PER_ENTRANT})\n"
                f"Balance: {player.credits} 💰 | Total wins: {player.wins}"
            )
        )
        return actions

    # -- periodic timers ------------------------------------------------------------------
    def tick(self, now: float) -> list[Action]:
        """Advance timers; the caller executes the returned posts/edits each poll."""
        try:
            actions: list[Action] = []
            if self.quiz is not None and now - self.quiz.asked_at > self.quiz_seconds:
                quiz = self.quiz
                self.quiz = None
                self.last_quiz_resolved_at = now
                self.save()
                correct = quiz.question.options[quiz.question.answer_index]
                fact_line = f"\n💡 {quiz.question.fact}" if quiz.question.fact else ""
                actions.append(
                    Post(
                        f"⏰ Quiz er shomoy sesh! Keu shothik jawab dite parini.\n"
                        f"Shothik jawab chhilo {_LETTERS[quiz.question.answer_index]}) {correct}.{fact_line}\n"
                        f"Notun quiz er jonno `/quiz` dao!"
                    )
                )
            if self.guess is not None and now - self.guess.started_at > self.guess_seconds:
                guess = self.guess
                self.guess = None
                self.save()
                actions.append(
                    Post(
                        f"⏰ Guess round shesh! Number chhilo {guess.target} "
                        f"(mot {guess.attempts} ta guess hoyechhilo). Notun round e `/guess` dao!"
                    )
                )
            if self.race is not None:
                race = self.race
                if race.phase == "lobby" and now - race.opened_at >= self.race_lobby_seconds:
                    if len(race.entrants) < RACE_MIN_ENTRANTS:
                        self.race = None
                        # A cancelled race is not a real race: shorten the cooldown
                        # so the group can open a fresh lobby almost immediately.
                        self.last_race_finished_at = now - RACE_COOLDOWN_SECONDS + RACE_CANCEL_COOLDOWN_SECONDS
                        actions.append(
                            Post(
                                f"😢 {race.anchor} cancel — minimum {RACE_MIN_ENTRANTS} jon join kore ni "
                                f"(join korlo {len(race.entrants)}). Notun lobby er jonno `/race` dao!"
                            )
                        )
                    else:
                        actions.extend(self._start_race(race, now))
                elif race.phase == "running" and now - race.last_frame_at >= self.race_frame_seconds:
                    actions.extend(self._advance_race(race, now))
            return actions
        except Exception as error:  # noqa: BLE001 — timers must never kill the monitor loop
            print(f"Game tick failed: {error}")
            return []
