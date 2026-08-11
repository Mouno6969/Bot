import asyncio

from messenger_bot.config import Settings
from messenger_bot.media import ManusMediaClient


async def main():
    client = ManusMediaClient(Settings.from_env())
    reply = await client.reply("Reply with exactly: MANUS_API_SMOKE_OK")
    print(reply)


if __name__ == "__main__":
    asyncio.run(main())
