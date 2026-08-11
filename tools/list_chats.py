"""List Messenger chat threads visible to the bot's browser profile.

Prints each chat's name and /messages/t/<id>/ URL so a group URL can be copied
into MESSENGER_GROUP_URL in .env. Reads the local session only; prints nothing
sensitive. Stop the bot before running (the profile is locked while it runs).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from messenger_bot.config import Settings


async def main() -> int:
    settings = Settings.from_env()
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.user_data_dir),
            headless=True,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://www.facebook.com/messages/", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        raw = await page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll('a[href*="/messages/t/"]').forEach(a => {
                    out.push({
                        href: a.getAttribute('href') || '',
                        label: (a.getAttribute('aria-label') || a.innerText || '').trim(),
                    });
                });
                return out;
            }"""
        )
        await context.close()

    seen = {}
    for item in raw:
        match = re.search(r"/messages/t/(\d+)", item["href"])
        if not match:
            continue
        thread_id = match.group(1)
        name = item["label"].split("\n")[0].strip() or "(unnamed)"
        seen.setdefault(thread_id, name)

    chats = [
        {"name": name, "url": f"https://www.facebook.com/messages/t/{tid}/"}
        for tid, name in seen.items()
    ]
    print(json.dumps(chats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
