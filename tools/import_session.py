"""One-time helper: import Facebook session cookies into the bot's browser profile.

Run it with the cookie values passed through environment variables so they never
touch Git or shell history files:

    FB_C_USER=... FB_XS=... python3 tools/import_session.py

Optional extra cookies reduce the chance of a Facebook security checkpoint:
    FB_DATR=... FB_SB=... FB_FR=...

The script launches the bot's own persistent Chromium profile, injects the
cookies, opens Messenger, and verifies that the session is logged in. Cookie
values are never printed. A verification screenshot is written to
/tmp/messenger_session_check.png for visual confirmation.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from messenger_bot.config import Settings

COOKIE_SPEC = [
    ("FB_C_USER", "c_user", True),
    ("FB_XS", "xs", True),
    ("FB_DATR", "datr", False),
    ("FB_SB", "sb", False),
    ("FB_FR", "fr", False),
]

SCREENSHOT_PATH = "/tmp/messenger_session_check.png"


async def main() -> int:
    settings = Settings.from_env()

    cookies = []
    missing = []
    for env_name, cookie_name, required in COOKIE_SPEC:
        value = os.getenv(env_name, "").strip()
        if not value:
            if required:
                missing.append(env_name)
            continue
        cookies.append(
            {
                "name": cookie_name,
                "value": value,
                "domain": ".facebook.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        )
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        return 2

    print(f"Injecting {len(cookies)} cookies into profile: {settings.user_data_dir}")

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.user_data_dir),
            headless=True,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        await context.add_cookies(cookies)

        page = context.pages[0] if context.pages else await context.new_page()
        # Verify on facebook.com itself: .facebook.com cookies are NOT sent to
        # messenger.com, which always shows a public landing page to this profile.
        target = os.getenv("FB_CHECK_URL", "https://www.facebook.com/messages/")
        await page.goto(target, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)

        final_url = page.url
        title = await page.title()
        body_text = ""
        try:
            body_text = (await page.inner_text("body"))[:2000]
        except Exception:
            pass

        await page.screenshot(path=SCREENSHOT_PATH, full_page=False)
        await context.close()

    print(f"Checked   : {target}")
    print(f"Final URL : {final_url}")
    print(f"Page title: {title}")

    logged_out_url = "login" in final_url.lower() or "checkpoint" in final_url.lower()
    login_form = "email address or phone number" in body_text.lower()
    if logged_out_url or login_form:
        print("RESULT: NOT logged in — Facebook shows a login/checkpoint page.")
        print("The cookies were rejected (expired, copied incorrectly, or blocked by a security check).")
        return 1

    print("RESULT: LOGGED IN ✅ — facebook.com loaded without a login redirect.")
    print(f"Visual proof: {SCREENSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
