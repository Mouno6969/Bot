"""Message routing for explicit media commands and normal mentions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class RequestKind(str, Enum):
    CHAT = "chat"
    IMAGE = "image"
    VOICE = "voice"
    EDIT = "edit"
    SING = "sing"
    HELP = "help"


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
COMMAND_PATTERN = re.compile(r"/(image|voice|edit|sing|help)\b[ \t]*([^\n\r]*)", re.IGNORECASE)


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
    return RoutedRequest(RequestKind(command.casefold()), normalize_text(argument))


def help_text() -> str:
    return (
        "Commands: /image <description>, /voice <text>, /sing <brief or lyrics>, "
        "and /edit <instruction> with an image attached in the same message. "
        "For all other questions, just mention me."
    )


def missing_argument_text(kind: RequestKind) -> str:
    examples = {
        RequestKind.IMAGE: "@Shahidulla /image a rainy Dhaka street at night",
        RequestKind.VOICE: "@Shahidulla /voice আজকে সবাই কেমন আছো?",
        RequestKind.SING: "@Shahidulla /sing a 45-second upbeat Banglish friendship song",
        RequestKind.EDIT: "attach an image and write: @Shahidulla /edit make it a watercolor portrait",
    }
    return f"Please add details. Example: {examples[kind]}"
