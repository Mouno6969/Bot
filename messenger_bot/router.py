"""Message routing for explicit media commands and normal mentions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from urllib.parse import urlparse


class RequestKind(str, Enum):
    CHAT = "chat"
    IMAGE = "image"
    VOICE = "voice"
    EDIT = "edit"
    SING = "sing"
    HELP = "help"
    CALCULATE = "calculate"
    VIDEO = "video"
    MUSICVIDEO = "musicvideo"
    LINK = "link"
    GAMES = "games"
    QUIZ = "quiz"
    ANSWER = "answer"
    RACE = "race"
    GUESS = "guess"
    FLIP = "flip"
    BALANCE = "balance"
    RANKING = "ranking"
    DAILY = "daily"


# Command kinds that never call a model or media API — they run entirely inside
# the local game engine and reply instantly.
GAME_KINDS = (
    RequestKind.GAMES,
    RequestKind.QUIZ,
    RequestKind.ANSWER,
    RequestKind.RACE,
    RequestKind.GUESS,
    RequestKind.FLIP,
    RequestKind.BALANCE,
    RequestKind.RANKING,
    RequestKind.DAILY,
)


@dataclass(frozen=True)
class RoutedRequest:
    kind: RequestKind
    argument: str


# The command argument is bounded to the SAME line as the command. Messenger's
# accessibility scrape appends UI chrome (timestamps, sender names, "Compose",
# "Chat members", "Privacy & support", …) on the lines that follow a message, so
# a DOTALL ".*" would swallow all of it into the argument — which the media
# generator then dutifully reads aloud or sings. Stopping at the newline keeps
# only what the user actually typed after the command.
COMMAND_PATTERN = re.compile(
    r"/(image|voice|edit|sing|help|calculate|stats|musicvideo|video|link"
    r"|games|quiz|answer|race|guess|flip|balance|credits|wallet"
    r"|ranking|leaderboard|rank|daily)\b[ \t]*([^\n\r]*)",
    re.IGNORECASE,
)

# "/stats" is a friendly alias for "/calculate"; game commands get short aliases too.
_COMMAND_ALIASES = {
    "stats": "calculate",
    "credits": "balance",
    "wallet": "balance",
    "leaderboard": "ranking",
    "rank": "ranking",
}


def normalize_text(text: str) -> str:
    return " ".join(text.replace("\u200b", " ").split())


def has_mention(text: str, bot_name: str) -> bool:
    """Recognize full-name and first-name mentions without treating ordinary text as a command."""
    lowered = text.casefold()
    first_name = bot_name.split()[0].casefold()
    return bot_name.casefold() in lowered or f"@{first_name}" in lowered


def parse_request(message_text: str) -> RoutedRequest:
    """Parse one explicit command; an uncommanded mention is a normal chat request."""
    match = COMMAND_PATTERN.search(message_text)
    if not match:
        return RoutedRequest(RequestKind.CHAT, "")

    command, argument = match.groups()
    command = _COMMAND_ALIASES.get(command.casefold(), command.casefold())
    argument = normalize_text(argument)
    # Confirmation is intentionally a separate command with no URL. Handle it
    # before parse_link_selection(), which correctly requires a Facebook URL for
    # every other /link form.
    if command == "link" and argument.casefold() == "confirm":
        return RoutedRequest(RequestKind.LINK, "confirm")
    return RoutedRequest(RequestKind(command), argument)


def help_text() -> str:
    return (
        "Commands: /image <description>, "
        "/video <prompt> (turns your idea into a short cinematic clip: scenes + spoken voiceover), "
        "/musicvideo <prompt> (a short clip with scenes + an original song), "
        "/voice <text>, /sing <brief or lyrics>, /link <Facebook URL> <option path> [quantity <number>] [submit], "
        "/edit <instruction> with an image attached in the same message, and "
        "/calculate for the group message count and per-member ranking. "
        "Games use credits and a leaderboard — try /games for the full list. "
        "For all other questions, just mention me."
    )


def games_help_text() -> str:
    return (
        "🎮 GAME ZONE — shobai credits khelo! 🎮\n"
        "• /quiz [category] — multiple-choice quiz; prothom shothik jawab pabe credits "
        f"(category: general, science, bangladesh, sports, fun)\n"
        "• /answer <A|B|C|D> — active quiz er jawab dao\n"
        "• /race [lane] — horse race lobby te join koro; race track live edit hote thakbe, "
        "jitla pot! /race go diye creator agey start korte pare\n"
        "• /guess — ami 1-50 er moddhe number bhabbo; /guess <number> diye dhoro\n"
        "• /flip <heads|tails> <amount> — coin flip bet; jitla double, haarla bet jabe\n"
        "• /daily — protidiner free credits\n"
        "• /balance — tomar credits, wins, ar rank\n"
        "• /ranking — leaderboard: top 10 credit holders\n"
        "Notun player ra 100 starter credits pay. Shob command e amake mention koro!"
    )


def missing_argument_text(kind: RequestKind) -> str:
    examples = {
        RequestKind.IMAGE: "@Shahidulla /image a rainy Dhaka street at night",
        RequestKind.VIDEO: "@Shahidulla /video shobai ke shubho sokal",
        RequestKind.MUSICVIDEO: "@Shahidulla /musicvideo ekta upbeat bondhutturer gaan",
        RequestKind.VOICE: "@Shahidulla /voice আজকে সবাই কেমন আছো?",
        RequestKind.SING: "@Shahidulla /sing a 45-second upbeat Banglish friendship song",
        RequestKind.EDIT: "attach an image and write: @Shahidulla /edit make it a watercolor portrait",
        RequestKind.LINK: "@Shahidulla /link https://www.facebook.com/username 2 4 quantity 3 submit",
    }
    return f"Please add details. Example: {examples[kind]}"




def parse_link_selection(value: str) -> tuple[str, tuple[int, ...], bool, int] | None:
    """Return a URL, positional path, submit flag, and repeat quantity.

    Supported forms are ``<url>`` followed by zero or more numeric choices,
    optionally followed by ``quantity <number>`` and optionally ending in the
    exact word ``submit``. Every option choice must be between 1 and 10;
    quantity must be a positive integer. A missing path defaults to ``(1,)``
    and a missing quantity defaults to ``1``.
    """
    parts = normalize_text(value).split()
    if not parts:
        return None
    submit = parts[-1].casefold() == "submit"
    if submit:
        parts.pop()

    quantity = 1
    if len(parts) >= 2 and parts[-2].casefold() == "quantity":
        if not parts[-1].isdigit():
            return None
        quantity = int(parts[-1])
        parts = parts[:-2]
    elif any(part.casefold() == "quantity" for part in parts[1:]):
        return None
    if quantity < 1:
        return None

    numeric = parts[1:]
    if not numeric or not all(token.isdigit() for token in numeric):
        if numeric:
            return None
    numbers = tuple(int(token) for token in numeric)
    if any(not 1 <= number <= 10 for number in numbers):
        return None
    return parts[0], numbers or (1,), submit, quantity


def is_facebook_url(value: str) -> bool:
    """Return whether value is a safe HTTPS URL hosted on Facebook."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    return (
        parsed.scheme.casefold() == "https"
        and (hostname == "facebook.com" or hostname.endswith(".facebook.com"))
    )
