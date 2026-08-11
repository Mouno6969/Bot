"""Entry point for the Shahidulla Kaysar Messenger bot."""

from __future__ import annotations

import asyncio

from messenger_bot.config import Settings
from messenger_bot.messenger import MessengerBot


def main() -> None:
    settings = Settings.from_env()
    asyncio.run(MessengerBot(settings).run())


if __name__ == "__main__":
    main()
