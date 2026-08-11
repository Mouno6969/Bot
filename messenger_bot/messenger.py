"""Playwright Messenger monitor with deduplicated command and chat handling."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import requests
from playwright.async_api import BrowserContext, Page, async_playwright

from .config import Settings
from .media import ManusMediaClient, MediaAsset, MediaError
from .router import RequestKind, RoutedRequest, has_mention, help_text, missing_argument_text, parse_request


# Messenger virtualizes its message list: older messages only render after scrolling
# up and are dropped again when scrolling down. These snippets find the conversation's
# scroll container (the tallest scrollable element inside role="main") so the bot can
# page upward and collect history into a persistent transcript.
_PICK_SCROLLER_JS = """
() => {
  const main = document.querySelector('[role="main"]') || document.body;
  let best = main;
  for (const el of main.querySelectorAll('div')) {
    if (el.scrollHeight > el.clientHeight + 50 && el.clientHeight > 250) {
      if (best === main || el.scrollHeight > best.scrollHeight) best = el;
    }
  }
  window.__botScroller = best;
  return true;
}
"""
_SCROLL_UP_JS = """
() => {
  const el = window.__botScroller || document.scrollingElement;
  if (!el) return null;
  el.scrollTop = 0;
  const r = el.getBoundingClientRect();
  return {x: r.x + r.width / 2, y: r.y + Math.min(r.height / 2, 200)};
}
"""
_READ_SCROLLER_JS = """
() => {
  const el = window.__botScroller;
  if (el && (el.innerText || '').length > 0) return el.innerText;
  return document.body ? document.body.innerText : '';
}
"""
_SCROLL_BOTTOM_JS = """
() => {
  const el = window.__botScroller;
  if (el) el.scrollTop = el.scrollHeight;
}
"""


@dataclass
class BotState:
    processed: deque[str] = field(default_factory=lambda: deque(maxlen=80))
    last_reply: str = ""

    @classmethod
    def load(cls, path: Path) -> "BotState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(deque(raw.get("processed", []), maxlen=80), raw.get("last_reply", ""))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps({"processed": list(self.processed), "last_reply": self.last_reply}, ensure_ascii=False),
            encoding="utf-8",
        )


class MessengerBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = BotState.load(settings.state_file)
        self.media = ManusMediaClient(settings)
        self.last_observed_text = ""
        self.transcript = self._load_transcript()

    async def run(self) -> None:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.settings.user_data_dir),
                headless=True,
                viewport={"width": 1280, "height": 800},
            )
            await self._configure_browser(browser)
            page = await browser.new_page()
            try:
                await self._open_group(page)
                await self._monitor(page)
            finally:
                await browser.close()

    async def _configure_browser(self, browser: BrowserContext) -> None:
        # Images must remain available in the page because `/edit` needs the most recent attachment.
        await browser.route("**/*.{woff,woff2}", lambda route: route.abort())

    async def _open_group(self, page: Page) -> None:
        print(f"Opening Messenger group: {self.settings.group_url}")
        await page.goto(self.settings.group_url, wait_until="commit", timeout=90_000)
        await asyncio.sleep(15)
        await self._unlock_if_needed(page)
        await self._load_full_history(page)

    async def _unlock_if_needed(self, page: Page) -> None:
        if not self.settings.encryption_pin:
            return
        selectors = "input[type='password'], input[aria-label*='PIN'], input[placeholder*='PIN']"
        pin_input = page.locator(selectors).first
        try:
            if await pin_input.count():
                print("Entering Messenger encryption PIN.")
                await pin_input.fill(self.settings.encryption_pin)
                await page.keyboard.press("Enter")
                await asyncio.sleep(8)
        except Exception as error:
            print(f"PIN entry was not completed: {error}")

    async def _load_full_history(self, page: Page) -> None:
        """Scroll up through the conversation and collect older messages into the transcript."""
        if self.settings.history_scrolls < 1:
            return
        print("Loading older group messages for context...")
        try:
            await page.evaluate(_PICK_SCROLLER_JS)
            # Round 0 reads the currently visible (newest) view without scrolling.
            first = await page.evaluate(_READ_SCROLLER_JS)
            self.transcript = self._merge_newer(self.transcript, first)
            stale_rounds = 0
            for round_no in range(1, self.settings.history_scrolls + 1):
                before = len(self.transcript)
                await page.evaluate(_PICK_SCROLLER_JS)
                point = await page.evaluate(_SCROLL_UP_JS)
                if point:
                    try:
                        await page.mouse.move(point["x"], point["y"])
                        await page.mouse.wheel(0, -2400)
                    except Exception:
                        pass
                await asyncio.sleep(1.4)
                await page.evaluate(_PICK_SCROLLER_JS)
                fragment = await page.evaluate(_READ_SCROLLER_JS)
                self.transcript = self._merge_older(self.transcript, fragment)
                stale_rounds = stale_rounds + 1 if len(self.transcript) <= before + 10 else 0
                if stale_rounds >= 3:
                    break
        except Exception as error:
            print(f"History load skipped: {error}")
        finally:
            try:
                await page.evaluate(_PICK_SCROLLER_JS)
                await page.evaluate(_SCROLL_BOTTOM_JS)
            except Exception:
                pass
            if len(self.transcript) > self.settings.history_characters:
                self.transcript = self.transcript[-self.settings.history_characters :]
            self._save_transcript()
        print(f"Conversation history ready: {len(self.transcript)} characters.")

    def _load_transcript(self) -> str:
        try:
            text = self.settings.history_file.read_text(encoding="utf-8")
            return text[-self.settings.history_characters :]
        except (FileNotFoundError, OSError):
            return ""

    def _save_transcript(self) -> None:
        try:
            self.settings.history_file.write_text(
                self.transcript[-self.settings.history_characters :], encoding="utf-8"
            )
        except OSError as error:
            print(f"Could not save conversation history: {error}")

    def _append_transcript(self, new_text: str) -> None:
        self.transcript = self._merge_newer(self.transcript, new_text)
        if len(self.transcript) > self.settings.history_characters:
            self.transcript = self.transcript[-self.settings.history_characters :]
        self._save_transcript()

    @staticmethod
    def _merge_newer(base: str, fragment: str) -> str:
        """Attach a newer-or-equal fragment after base, removing the duplicated overlap."""
        if not fragment:
            return base
        if not base:
            return fragment
        if fragment in base:
            return base
        if base in fragment:
            return fragment
        for k in range(min(len(base), len(fragment)), 19, -1):
            if base.endswith(fragment[:k]):
                return base + fragment[k:]
        return base + "\n" + fragment

    @staticmethod
    def _merge_older(base: str, fragment: str) -> str:
        """Attach an older fragment before base, removing the duplicated overlap."""
        if not fragment:
            return base
        if not base:
            return fragment
        if fragment in base:
            return base
        if base in fragment:
            return fragment
        for k in range(min(len(base), len(fragment)), 19, -1):
            if base.startswith(fragment[-k:]):
                return fragment + base[k:]
        return fragment + "\n" + base

    async def _monitor(self, page: Page) -> None:
        self.last_observed_text = await self._page_text(page)
        print("MONITORING ACTIVE. Existing messages are checkpointed; only new mentions will be handled.")

        while True:
            try:
                full_text = await self._page_text(page)
                if not full_text:
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                new_text = self._newly_appended_text(self.last_observed_text, full_text)
                self.last_observed_text = full_text
                if not new_text:
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                self._append_transcript(new_text)
                context = self.transcript[-self.settings.context_characters :]
                fingerprint = self._fingerprint(new_text)

                if fingerprint in self.state.processed:
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue
                if not has_mention(new_text, self.settings.bot_name):
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue
                if self._is_self_activity(new_text):
                    self._remember(fingerprint)
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                print("NEW MENTION DETECTED")
                await self._handle_request(page, context, new_text)
                # Mark a message only after its response has been successfully delivered.
                self._remember(fingerprint)
                await asyncio.sleep(5)
            except Exception as error:
                print(f"Monitor loop error: {error}")
                await asyncio.sleep(10)

    async def _handle_request(self, page: Page, context: str, recent: str) -> None:
        request = parse_request(recent)
        if request.kind == RequestKind.HELP:
            await self._send_text(page, help_text())
            return
        if request.kind == RequestKind.CHAT:
            answer = await self._answer_mention(context)
            await self._send_text(page, answer)
            return
        if not request.argument:
            await self._send_text(page, missing_argument_text(request.kind))
            return

        if request.kind == RequestKind.EDIT:
            source_image = await self._download_recent_chat_image(page)
            if source_image is None:
                await self._send_text(
                    page,
                    "For /edit, attach one image in the same message and write the edit instruction after /edit.",
                )
                return
        else:
            source_image = None

        acknowledgement = {
            RequestKind.IMAGE: "Image ta banachhi—ektu wait koro.",
            RequestKind.VOICE: "Voice note ta toiri korchhi—ektu wait koro.",
            RequestKind.SING: "Original song ta banachhi—ektu wait koro.",
            RequestKind.EDIT: "Image ta edit korchhi—ektu wait koro.",
        }[request.kind]
        await self._send_text(page, acknowledgement)

        try:
            asset = await self.media.generate(request.kind, request.argument, source_image)
            await self._send_media(page, asset)
            self.state.last_reply = acknowledgement
            self.state.save(self.settings.state_file)
            print(f"Delivered {request.kind.value}: {asset.filename}")
        except MediaError as error:
            print(f"Media job failed: {error}")
            await self._send_text(page, f"Sorry, {request.kind.value} ta complete korte parlam na. {error}")

    async def _answer_mention(self, context: str) -> str:
        prompt = f"""You are {self.settings.bot_name}, replying inside a Messenger group.

Conversation history (oldest at the top, newest at the bottom):
---
{context}
---

Reply only to the newest question or request addressed to you. Match the user’s language and script exactly: Bengali, Banglish, or English. Be concise, useful, and natural.

For comparisons about fluency, manners, skills, or behavior, make claims only when the conversation history provides direct support. Name the observable examples briefly. If the history does not provide enough evidence, say that clearly instead of inventing a ranking, member fact, or history. Do not generate image, voice, song, or edit requests in normal chat—tell users to use the explicit slash command if relevant. Output only the message that should be posted."""
        try:
            answer = (await self.media.reply(prompt)).strip()
            return answer or "I could not prepare a reply just now—please mention me again."
        except MediaError as error:
            print(f"Manus reply error: {error}")
            return "দুঃখিত, এখন উত্তরটা তৈরি করতে পারছি না। একটু পরে আবার mention দাও।"

    async def _send_text(self, page: Page, text: str) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                input_box = await self._composer(page)
                await input_box.click(timeout=15_000)
                await input_box.fill(text, timeout=15_000)
                await input_box.press("Enter", timeout=15_000)
                self.state.last_reply = text
                self.state.save(self.settings.state_file)
                print(f"Text reply sent: {text[:90]}")
                return
            except Exception as error:
                last_error = error
                print(f"Composer send attempt {attempt} failed: {error}")
                await self._recover_chat(page)
        raise RuntimeError(f"Messenger composer unavailable after 3 attempts: {last_error}")

    async def _composer(self, page: Page):
        selectors = [
            "div[role='textbox'][contenteditable='true']",
            "[role='textbox'][contenteditable='true']",
            "div[contenteditable='true']",
        ]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for selector in selectors:
                candidates = page.locator(selector)
                count = await candidates.count()
                for index in range(count - 1, -1, -1):
                    candidate = candidates.nth(index)
                    if await candidate.is_visible():
                        return candidate
            await asyncio.sleep(1)
        raise RuntimeError("No visible Messenger message composer was found.")

    async def _recover_chat(self, page: Page) -> None:
        try:
            await page.goto(self.settings.group_url, wait_until="commit", timeout=60_000)
            await asyncio.sleep(8)
            await self._unlock_if_needed(page)
        except Exception as error:
            print(f"Chat recovery navigation failed: {error}")

    async def _send_media(self, page: Page, asset: MediaAsset) -> None:
        file_inputs = page.locator("input[type='file']")
        try:
            if await file_inputs.count():
                await file_inputs.last.set_input_files(str(asset.local_path))
            else:
                attach_button = page.locator("[aria-label='Attach a file'], [aria-label*='Attach']").first
                async with page.expect_file_chooser() as chooser_info:
                    await attach_button.click()
                chooser = await chooser_info.value
                await chooser.set_files(str(asset.local_path))
            await asyncio.sleep(3)
            composer = await self._composer(page)
            await composer.press("Enter", timeout=15_000)
        except Exception as error:
            raise MediaError(f"The media was generated but Messenger could not upload it: {error}") from error

    async def _download_recent_chat_image(self, page: Page) -> Path | None:
        """Download the most recent large non-avatar Messenger image to use as `/edit` input."""
        candidates: list[dict[str, Any]] = await page.locator("img").evaluate_all(
            """imgs => imgs.map(img => ({
                src: img.currentSrc || img.src,
                alt: img.alt || '',
                width: img.naturalWidth || img.width || 0,
                height: img.naturalHeight || img.height || 0
            }))"""
        )
        for item in reversed(candidates):
            src = str(item.get("src", ""))
            alt = str(item.get("alt", "")).casefold()
            if not src.startswith("http"):
                continue
            if item.get("width", 0) < 160 or item.get("height", 0) < 160:
                continue
            if any(word in alt for word in ("profile", "avatar", "photo of")):
                continue
            try:
                response = await asyncio.to_thread(requests.get, src, timeout=45)
                response.raise_for_status()
                suffix = ".jpg"
                content_type = response.headers.get("Content-Type", "")
                if "png" in content_type:
                    suffix = ".png"
                path = self.settings.output_dir / f"edit_source_{int(time.time())}{suffix}"
                path.write_bytes(response.content)
                return path
            except Exception as error:
                print(f"Could not download a candidate image: {error}")
        return None

    async def _page_text(self, page: Page) -> str:
        return await page.evaluate("document.body ? document.body.innerText : ''")

    def _is_self_activity(self, recent: str) -> bool:
        tail = recent[-350:]
        if "You sent," in tail or f"{self.settings.bot_name} sent," in tail:
            return True
        return bool(self.state.last_reply and self.state.last_reply in tail)

    def _remember(self, fingerprint: str) -> None:
        self.state.processed.append(fingerprint)
        self.state.save(self.settings.state_file)

    @staticmethod
    def _newly_appended_text(previous: str, current: str) -> str:
        """Return only the newly appended Messenger text, with a safe fallback on DOM rewrites."""
        if current == previous:
            return ""
        if previous and current.startswith(previous):
            return current[len(previous) :]
        # Messenger may rewrite parts of its accessible DOM; if so, process only a small fresh tail.
        return current[-500:]

    @staticmethod
    def _fingerprint(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
