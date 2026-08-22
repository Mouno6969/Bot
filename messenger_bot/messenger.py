"""Playwright Messenger monitor with deduplicated command and chat handling."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

import requests
from playwright.async_api import BrowserContext, Page, async_playwright

from .config import Settings
from .media import ManusMediaClient, MediaAsset, MediaError
from .meta_ai import MetaChatClient, MetaError
from .router import (
    RequestKind,
    RoutedRequest,
    has_mention,
    help_text,
    is_facebook_url,
    missing_argument_text,
    parse_link_selection,
    parse_request,
)
from .video_plan import VideoPlan, build_planner_prompt, parse_plan


# Messenger virtualizes its message list: older messages only render after scrolling
# up and are dropped again when scrolling down. These snippets find the conversation's
# scroll container (the tallest scrollable element inside role="main") so the bot can
# page upward and collect history into a persistent transcript.
_FACEBOOK_TEXT_OPTION_SELECTOR = (
    '[role="option"]:visible, '
    '[role="button"]:visible, '
    '[role="menuitem"]:visible, '
    'div[tabindex="0"]:visible'
)

_CLICK_FACEBOOK_OPTION_JS = r"""
({index, mode}) => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const uniqueRows = (root) => {
    const rows = Array.from(root.querySelectorAll('[role="menuitem"], [role="option"], [role="button"]'))
      .filter(visible)
      .map((el) => ({el, text: clean(el.innerText || el.textContent)}))
      .filter(({text}) => text);
    const seen = new Set();
    return rows.filter(({el, text}) => {
      const rect = el.getBoundingClientRect();
      const key = `${text}|${Math.round(rect.x)}|${Math.round(rect.y)}|${Math.round(rect.width)}|${Math.round(rect.height)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  };
  const visibleContainers = (selector) => Array.from(document.querySelectorAll(selector)).filter(visible);
  const clickFrom = (root, rows) => {
    if (!rows || rows.length <= index) return {clicked: false, count: rows ? rows.length : 0, labels: rows ? rows.map(r => r.text) : []};
    rows[index].el.click();
    return {clicked: true, count: rows.length, labels: rows.map(r => r.text)};
  };

  if (mode === 'menu' || mode === 'active') {
    const menus = visibleContainers('[role="menu"]');
    if (menus.length) {
      const menu = menus[menus.length - 1];
      const result = clickFrom(menu, uniqueRows(menu));
      if (result.clicked || mode === 'menu') return {...result, container: 'menu'};
    }
  }

  if (mode === 'dialog' || mode === 'active') {
    const dialogs = visibleContainers('[role="dialog"], [aria-modal="true"]');
    if (dialogs.length) {
      const dialog = dialogs[dialogs.length - 1];
      const result = clickFrom(dialog, uniqueRows(dialog));
      if (result.clicked || mode === 'dialog') return {...result, container: 'dialog'};
    }

    // Facebook's report sheet in the live UI has no dialog role. Find its
    // visible question heading and walk upward to the smallest panel containing
    // the plain-text button rows.
    const headings = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"],div,span'))
      .filter(visible)
      .filter((el) => {
        const text = clean(el.innerText || el.textContent);
        return /^(Why are you reporting|What kind of |How is it |Who is being )/i.test(text);
      });
    for (const heading of headings) {
      let panel = heading;
      while (panel && panel !== document.body) {
        const rows = uniqueRows(panel);
        if (rows.length >= 2) {
          const result = clickFrom(panel, rows);
          return {...result, container: 'report-sheet'};
        }
        panel = panel.parentElement;
      }
    }
  }
  return {clicked: false, count: 0, labels: []};
}
"""

_CLICK_FACEBOOK_REPORT_PROFILE_JS = r"""
() => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const menus = Array.from(document.querySelectorAll('[role="menu"]')).filter(visible);
  if (!menus.length) return {clicked: false, count: 0, labels: []};
  const menu = menus[menus.length - 1];
  const rows = Array.from(menu.querySelectorAll('[role="menuitem"], [role="option"], [role="button"]'))
    .filter(visible)
    .map((el) => ({el, text: clean(el.innerText || el.textContent || el.getAttribute('aria-label'))}))
    .filter(({text}) => text);
  const labels = rows.map(({text}) => text);
  const matches = rows.filter(({text}) => /^report(?: this)? profile$/i.test(text));
  if (matches.length !== 1) return {clicked: false, count: matches.length, labels};
  matches[0].el.click();
  return {clicked: true, count: 1, labels};
}
"""


_CHAT_PROMPT_MAX_CHARACTERS = 7000
_CHAT_ROSTER_MAX_CHARACTERS = 1200
# Messenger occasionally leaves a headless page with a frozen accessibility DOM.
# Refresh after roughly one minute of no DOM change so new messages become visible.
_MONITOR_REFRESH_IDLE_POLLS = 12


_CLICK_FACEBOOK_SUBMIT_JS = r"""
(confirm) => {
  const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
  const visible = (el) => {
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'))
    .filter(visible)
    .filter((el) => clean(el.innerText || el.textContent) === 'Submit' || clean(el.getAttribute('aria-label')) === 'Submit');
  if (buttons.length !== 1) return {ready: false, count: buttons.length};
  if (confirm) buttons[0].click();
  return {ready: true, clicked: Boolean(confirm)};
}
"""


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

# --- Native "Reply" threading -------------------------------------------------------
# Detection is text-only, so to reply to the specific triggering message we must
# re-locate it in the DOM by its text at send-time. A message is a role="button" whose
# accessible name is "Enter, Message sent HH:MM by NAME: text"; its real bubble is the
# closest role="article" (the tiny button rect is just an inner icon). We tag that
# article, physically hover its centre — Messenger only reveals the hover action
# toolbar for a REAL pointer move, not a synthetic event or a forced .hover() — then
# click the article-scoped "Reply" control so we thread to the correct message.
_TAG_REPLY_TARGET_JS = r"""
(anchor) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const needle = norm(anchor);
  if (!needle) return null;
  document.querySelectorAll('[data-reply-target]').forEach(e => e.removeAttribute('data-reply-target'));
  const btns = Array.from(document.querySelectorAll('[role="button"][aria-label*="Message sent"]'));
  // Newest match = the trigger; scan from the end.
  const target = btns.reverse().find(b => norm(b.getAttribute('aria-label')).includes(needle));
  if (!target) return null;
  const article = target.closest('[role="article"]') || target.parentElement;
  if (!article) return null;
  article.setAttribute('data-reply-target', '1');
  article.scrollIntoView({block: 'center'});
  return true;
}
"""
_REPLY_TARGET_RECT_JS = r"""
() => {
  const a = document.querySelector('[data-reply-target]');
  if (!a) return null;
  const r = a.getBoundingClientRect();
  return {x: r.left, y: r.top, w: r.width, h: r.height};
}
"""
# Click the reply control INSIDE the tagged article only, so we never grab a different
# message's leftover hover toolbar. Prefer the direct button; else open "More actions".
_CLICK_REPLY_IN_ARTICLE_JS = r"""
() => {
  const art = document.querySelector('[data-reply-target]');
  if (!art) return {path: 'no-article'};
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const direct = Array.from(art.querySelectorAll('[role="button"][aria-label="Reply to this message"]')).find(visible);
  if (direct) { direct.click(); return {path: 'direct'}; }
  const more = Array.from(art.querySelectorAll('[role="button"][aria-label="More actions"]')).find(visible);
  if (more) { more.click(); return {path: 'opened-more'}; }
  return {path: 'no-control'};
}
"""
_CLICK_REPLY_MENUITEM_JS = r"""
() => {
  const items = Array.from(document.querySelectorAll('[role="menuitem"]'));
  const reply = items.find(m => {
    const t = (m.getAttribute('aria-label') || m.innerText || '').trim().toLowerCase();
    return t === 'reply' || t.startsWith('reply');
  });
  if (reply) { reply.click(); return true; }
  return false;
}
"""
# The composer shows a "Replying to <name>" preview once reply mode is armed; its
# presence is our success signal.
_REPLYING_PREVIEW_JS = r"""
() => {
  for (const el of document.querySelectorAll('h3, span, div')) {
    const t = (el.innerText || '').trim();
    if (/^Replying to /i.test(t) && t.length < 160) return t.split('\n')[0];
  }
  return null;
}
"""

# --- Media send confirmation --------------------------------------------------------
# Attaching a file only stages a LOCAL preview in the composer; the message is not sent
# until that staged attachment clears (Messenger removes the preview once it accepts the
# send). A truly-staged attachment shows a VISIBLE, ENABLED "Remove attachment" /
# "Upload another file" control. Messenger also keeps a permanently HIDDEN, DISABLED
# "Delete" button in the DOM — the visibility/enabled gate below excludes it, so this
# signal is a reliable, media-type-agnostic proof of staging and (once it flips back to
# false) of delivery. Counting <video>/<img> tags is NOT reliable: the composer preview
# renders one too, outside the conversation log.
_MEDIA_STAGED_JS = r"""
() => {
  const usable = (el) => {
    if (!el) return false;
    if (el.getAttribute('aria-hidden') === 'true') return false;
    if (el.getAttribute('aria-disabled') === 'true') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const sels = ['[aria-label="Remove attachment"]', '[aria-label="Upload another file"]'];
  for (const s of sels) {
    for (const el of document.querySelectorAll(s)) { if (usable(el)) return true; }
  }
  return false;
}
"""
# Timeouts/attempts for the attach -> send -> confirm cycle (seconds). Generous because a
# large video can take a while to stage/upload; module-level so tests can shrink them.
_MEDIA_STAGE_TIMEOUT_S = 45.0
_MEDIA_CONFIRM_TIMEOUT_S = 90.0
_MEDIA_CLEAR_TIMEOUT_S = 10.0
_MEDIA_SETTLE_S = 1.0
_MEDIA_SEND_ATTEMPTS = 3

# Messenger's accessibility scrape labels every message with its author as
# "… Message sent HH:MM by NAME: text". Extracting NAME from the WHOLE transcript
# (not just the recent context window) lets the bot always know the full roster of
# who is in the group, even for members who last spoke far outside the recent window.
_SENDER_PATTERN = re.compile(r"Message sent[^\n]*? by ([^:\n]{1,60}?)(?::|$)", re.MULTILINE)

# A fuller parse that also captures the timestamp and message text. Used to COUNT
# messages: the transcript is stitched from overlapping scroll captures, so the same
# message can appear many times. Deduplicating on (time, sender, text) turns the noisy
# raw lines into a truthful per-member tally.
_MESSAGE_PATTERN = re.compile(
    r"Message sent\s*(\d{1,2}:\d{2})?\s*by ([^:\n]{1,60}?)(?:: ?(.*))?$", re.MULTILINE
)


def extract_members(transcript: str, max_members: int = 40) -> list[str]:
    """Return distinct group members, most active first, from the full transcript."""
    counts: dict[str, int] = {}
    for name in _SENDER_PATTERN.findall(transcript):
        name = " ".join(name.split()).strip()
        # "You" is the bot's own account; it is not a group member to describe.
        if not name or name.casefold() == "you":
            continue
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts, key=lambda n: (-counts[n], n))
    return ranked[:max_members]


def count_messages(transcript: str) -> tuple[int, list[tuple[str, int]]]:
    """Count distinct messages and per-member totals from the full transcript.

    The transcript is assembled from overlapping scroll captures, so a single
    message can be recorded several times. We deduplicate on (time, sender, text)
    so the totals reflect real messages, not scrape artifacts. Boundary lines with
    no message text ("… by NAME" with no colon) are skipped. "You" is the bot's own
    account and is reported separately from member rankings by the caller.
    """
    seen: set[tuple[str, str, str]] = set()
    counts: dict[str, int] = {}
    total = 0
    for match in _MESSAGE_PATTERN.finditer(transcript):
        stamp = match.group(1) or ""
        name = " ".join(match.group(2).split()).strip()
        text = (match.group(3) or "").strip()
        if not name or not text:
            continue
        key = (stamp, name, text)
        if key in seen:
            continue
        seen.add(key)
        counts[name] = counts.get(name, 0) + 1
        total += 1
    ranking = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return total, ranking


def format_stats(transcript: str, bot_name: str) -> str:
    """Human-friendly message-count summary and per-member ranking for the group."""
    total, ranking = count_messages(transcript)
    if total == 0:
        return "Ekhono kono message count korte parini—history load hocche, ektu pore abar cheshta koro."
    lines = [f"📊 Group e mot {total} ta message hoyeche (shuru theke ekhon porjonto)."]
    lines.append("Ranking (ke koyta pathiyeche):")
    rank = 0
    for name, count in ranking:
        # The bot's own "You" account is not a group member; note it at the end instead.
        if name.casefold() == "you":
            continue
        rank += 1
        share = count * 100 / total
        lines.append(f"{rank}. {name} — {count} ({share:.0f}%)")
    me = next((c for n, c in ranking if n.casefold() == "you"), 0)
    if me:
        lines.append(f"(Ami nije {me} ta reply diyechi.)")
    lines.append("Note: purono message gulo jotota load kora geche tar upor base kore hisheb.")
    return "\n".join(lines)


@dataclass
class LinkVisitResult:
    status: str
    url: str
    option_path: tuple[int, ...]


@dataclass
class BotState:
    processed: deque[str] = field(default_factory=lambda: deque(maxlen=80))
    last_reply: str = ""
    pending_link_url: str = ""
    pending_link_path: tuple[int, ...] = ()
    pending_link_quantity: int = 0

    @classmethod
    def load(cls, path: Path) -> "BotState":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                deque(raw.get("processed", []), maxlen=80),
                raw.get("last_reply", ""),
                raw.get("pending_link_url", ""),
                tuple(raw.get("pending_link_path", [])),
                int(raw.get("pending_link_quantity", 0)),
            )
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "processed": list(self.processed),
                    "last_reply": self.last_reply,
                    "pending_link_url": self.pending_link_url,
                    "pending_link_path": list(self.pending_link_path),
                    "pending_link_quantity": self.pending_link_quantity,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


class MessengerBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = BotState.load(settings.state_file)
        self.media = ManusMediaClient(settings)
        self.meta = MetaChatClient(settings)
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
        idle_polls = 0
        print("MONITORING ACTIVE. Existing messages are checkpointed; only new mentions will be handled.")

        while True:
            try:
                # Keep the virtualized Messenger scroller at the newest messages;
                # this is independent of the user's phone/browser scroll position.
                await page.evaluate(_PICK_SCROLLER_JS)
                await page.evaluate(_SCROLL_BOTTOM_JS)
                full_text = await self._page_text(page)
                if not full_text:
                    idle_polls += 1
                    if idle_polls >= _MONITOR_REFRESH_IDLE_POLLS:
                        await self._refresh_monitor_page(page)
                        idle_polls = 0
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                new_text = self._newly_appended_text(self.last_observed_text, full_text)
                self.last_observed_text = full_text
                if not new_text:
                    idle_polls += 1
                    if idle_polls >= _MONITOR_REFRESH_IDLE_POLLS:
                        await self._refresh_monitor_page(page)
                        idle_polls = 0
                    await asyncio.sleep(self.settings.poll_interval_seconds)
                    continue

                idle_polls = 0
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

    async def _refresh_monitor_page(self, page: Page) -> None:
        """Reload Messenger after a prolonged unchanged DOM and restore the chat view."""
        print("Messenger DOM unchanged; refreshing the group page to recover live polling.")
        try:
            await page.reload(wait_until="commit", timeout=60_000)
            await asyncio.sleep(10)
            await self._unlock_if_needed(page)
            await page.evaluate(_PICK_SCROLLER_JS)
            await page.evaluate(_SCROLL_BOTTOM_JS)
            self.last_observed_text = await self._page_text(page)
            print("Messenger group refresh complete; monitoring resumed.")
        except Exception as error:
            print(f"Messenger group refresh failed; will retry: {error}")

    async def _handle_request(self, page: Page, context: str, recent: str) -> None:
        request = parse_request(recent)
        # Anchor every response — the immediate acknowledgement AND the eventual media —
        # to the specific message that triggered it, so replies stay threaded even when
        # other messages arrive during a multi-minute media job. "" -> plain send.
        reply_to = self._reply_anchor(recent)
        if request.kind == RequestKind.LINK and request.argument.casefold() == "confirm":
            if not self.state.pending_link_url or not self.state.pending_link_path:
                await self._send_text(page, "No pending Facebook submission confirm korte parini.", reply_to=reply_to)
                return
            pending_url = self.state.pending_link_url
            pending_path = self.state.pending_link_path
            remaining = self.state.pending_link_quantity or 1
            completed = 0
            try:
                for attempt in range(1, remaining + 1):
                    result = await self._visit_facebook_link(
                        page,
                        pending_url,
                        pending_path,
                        submit_requested=True,
                        confirm_submit=True,
                    )
                    completed += 1
                    self.state.pending_link_quantity = remaining - completed
                    self.state.save(self.settings.state_file)
            except Exception as error:  # noqa: BLE001 — report navigation failures to the chat
                print(f"Facebook submission {completed + 1}/{remaining} failed: {error}")
                await self._send_text(
                    page,
                    f"Facebook submit {completed + 1}/{remaining} korte parini; baki quantity pending ache.",
                    reply_to=reply_to,
                )
                return
            self.state.pending_link_url = ""
            self.state.pending_link_path = ()
            self.state.pending_link_quantity = 0
            self.state.save(self.settings.state_file)
            await self._send_text(
                page,
                f"Facebook submission {completed} ta complete korechi: option path {'.'.join(map(str, result.option_path))}.",
                reply_to=reply_to,
            )
            return
        if request.kind == RequestKind.HELP:
            await self._send_text(page, help_text(), reply_to=reply_to)
            return
        if request.kind == RequestKind.CALCULATE:
            # Deterministic local computation over the full transcript—no model call,
            # so it answers immediately and for free.
            await self._send_text(page, format_stats(self.transcript, self.settings.bot_name), reply_to=reply_to)
            return
        if request.kind == RequestKind.CHAT:
            answer = await self._answer_mention(context)
            await self._send_text(page, answer, reply_to=reply_to)
            return
        if not request.argument:
            await self._send_text(page, missing_argument_text(request.kind), reply_to=reply_to)
            return

        if request.kind == RequestKind.LINK:
            selection = parse_link_selection(request.argument)
            if selection is None or not is_facebook_url(selection[0]):
                await self._send_text(
                    page,
                    "Please send a valid HTTPS Facebook account link followed by "
                    "one or more option numbers from 1 to 10, for example: "
                    "https://www.facebook.com/username 2 4 1",
                    reply_to=reply_to,
                )
                return
            link_url, option_path, submit_requested, quantity = selection
            self.state.pending_link_url = ""
            self.state.pending_link_path = ()
            self.state.pending_link_quantity = 0
            try:
                result = await self._visit_facebook_link(
                    page,
                    link_url,
                    option_path,
                    submit_requested=submit_requested,
                    confirm_submit=False,
                )
            except Exception as error:  # noqa: BLE001 — report navigation failures to the chat
                print(f"Facebook link visit failed: {error}")
                await self._send_text(
                    page,
                    "Facebook link ta open korte parini. Link ta check kore abar try koro.",
                    reply_to=reply_to,
                )
                return
            if result.status == "awaiting_confirmation":
                self.state.pending_link_url = link_url
                self.state.pending_link_path = option_path
                self.state.pending_link_quantity = quantity
                self.state.save(self.settings.state_file)
                await self._send_text(
                    page,
                    "Submit button ready. Final action confirm korte `@Shahidulla /link confirm` pathao; "
                    f"option path {'.'.join(map(str, option_path))} quantity {quantity} submit hobe.",
                    reply_to=reply_to,
                )
            elif result.status == "submitted":
                await self._send_text(
                    page,
                    f"Facebook submission complete korechi: option path {'.'.join(map(str, option_path))}, quantity {quantity}.",
                    reply_to=reply_to,
                )
            else:
                await self._send_text(
                    page,
                    f"Facebook account link ta visit korechi: {result.url}",
                    reply_to=reply_to,
                )
            return

        if request.kind in (RequestKind.VIDEO, RequestKind.MUSICVIDEO):
            # Creative video: an LLM turns the prompt into an idea + 2-3 scene image
            # prompts + an extended voiceover/song script + a mood; we generate those
            # parts and stitch them into a moving, graded MP4 locally. /video narrates
            # with a spoken voice; /musicvideo uses an original song.
            is_music = request.kind == RequestKind.MUSICVIDEO
            audio_kind = RequestKind.SING if is_music else RequestKind.VOICE
            note = "gaan" if is_music else "voiceover"
            await self._send_text(
                page,
                f"Prompt ta niye idea + scene + {note} banachhi, tarpor video render korbo—"
                "koyek minute lagbe, wait koro.",
                reply_to=reply_to,
            )
            try:
                plan = await self._plan_video(request.argument, is_music)
                print(f"Video plan: mood={plan.mood}, {len(plan.scenes)} scene(s), idea={plan.idea[:80]!r}")
                asset = await self.media.generate_video(plan, audio_kind)
                await self._send_media(page, asset, reply_to=reply_to)
                self.state.last_reply = f"{request.kind.value} delivered"
                self.state.save(self.settings.state_file)
                print(f"Delivered {request.kind.value}: {asset.filename}")
            except MediaError as error:
                print(f"Video job failed: {error}")
                await self._send_text(page, f"Sorry, video ta banate parlam na. {error}", reply_to=reply_to)
            return

        if request.kind == RequestKind.EDIT:
            source_image = await self._download_recent_chat_image(page)
            if source_image is None:
                await self._send_text(
                    page,
                    "For /edit, attach one image in the same message and write the edit instruction after /edit.",
                    reply_to=reply_to,
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
        await self._send_text(page, acknowledgement, reply_to=reply_to)

        try:
            asset = await self.media.generate(request.kind, request.argument, source_image)
            await self._send_media(page, asset, reply_to=reply_to)
            self.state.last_reply = acknowledgement
            self.state.save(self.settings.state_file)
            print(f"Delivered {request.kind.value}: {asset.filename}")
        except MediaError as error:
            print(f"Media job failed: {error}")
            await self._send_text(
                page, f"Sorry, {request.kind.value} ta complete korte parlam na. {error}", reply_to=reply_to
            )

    async def _visit_facebook_link(
        self,
        page: Page,
        url: str,
        option_path: tuple[int, ...] = (1,),
        *,
        submit_requested: bool = False,
        confirm_submit: bool = False,
    ) -> LinkVisitResult:
        """Open a Facebook profile, click its three-dot menu, then close the tab."""
        tab = await page.context.new_page()
        try:
            response = await tab.goto(url, wait_until="domcontentloaded", timeout=30_000)
            if not is_facebook_url(tab.url):
                raise ValueError("Facebook URL redirected outside the Facebook domain")
            await asyncio.sleep(3)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"Facebook returned HTTP {response.status}")
            status = await self._click_facebook_more_menu(
                tab,
                option_path,
                submit_requested=submit_requested,
                confirm_submit=confirm_submit,
            )
            return LinkVisitResult(status, tab.url, option_path)
        finally:
            await tab.close()

    async def _click_facebook_more_menu(
        self,
        tab: Page,
        option_path: tuple[int, ...] = (1,),
        *,
        submit_requested: bool = False,
        confirm_submit: bool = False,
    ) -> str:
        """Follow a variable-depth path through Facebook's scoped text sheets."""
        if not option_path or any(not 1 <= number <= 10 for number in option_path):
            raise ValueError("Facebook option path numbers must each be between 1 and 10")

        selectors = (
            '[role="button"][aria-label="More options"]',
            '[role="button"][aria-label="See options"]',
            '[aria-label*="More options" i]',
            '[aria-label*="See options" i]',
            '[role="button"][aria-label*="More" i]',
            '[role="button"][aria-label*="Options" i]',
            '[role="button"][title*="More" i]',
        )
        for selector in selectors:
            controls = tab.locator(selector)
            for index in range(await controls.count()):
                control = controls.nth(index)
                if not await control.is_visible():
                    continue
                try:
                    await control.click(timeout=10_000)
                except Exception as error:
                    # Facebook can leave a transient light-mode overlay above the
                    # profile header. A DOM click is safe here because the control
                    # was already verified visible and uniquely selected.
                    print(f"Facebook More-options click intercepted; using DOM fallback: {error}")
                    await control.evaluate("(element) => element.click()")
                await asyncio.sleep(1)

                report_entry = await tab.evaluate(_CLICK_FACEBOOK_REPORT_PROFILE_JS)
                if not report_entry.get("clicked"):
                    raise RuntimeError(
                        "Facebook profile menu did not expose exactly one Report profile option: "
                        f"{report_entry.get('labels', [])}"
                    )
                await asyncio.sleep(1)

                report_prompt = await tab.evaluate(
                    "() => document.body.innerText.includes('Why are you reporting this profile?')"
                )
                scope_prompt = await tab.evaluate(
                    "() => document.body.innerText.includes('What do you want to report?')"
                )

                # Current Facebook UI first asks whether the report concerns the
                # profile or a specific post. /link targets profiles, so select
                # the first scope row before following the user-provided path.
                if scope_prompt:
                    scope = await tab.evaluate(
                        _CLICK_FACEBOOK_OPTION_JS,
                        {"index": 0, "mode": "dialog"},
                    )
                    if not scope.get("clicked"):
                        raise RuntimeError(
                            "Facebook report scope did not expose a profile option: "
                            f"{scope.get('labels', [])}"
                        )
                    await asyncio.sleep(1)
                    for depth, number in enumerate(option_path):
                        result = await tab.evaluate(
                            _CLICK_FACEBOOK_OPTION_JS,
                            {"index": number - 1, "mode": "dialog"},
                        )
                        if not result.get("clicked"):
                            raise RuntimeError(
                                f"Facebook nested option path failed at depth {depth + 1}: "
                                f"{result.get('labels', [])}"
                            )
                        if depth < len(option_path) - 1:
                            await asyncio.sleep(1)
                    return await self._finish_facebook_submission(
                        tab, option_path, submit_requested, confirm_submit
                    )

                if report_prompt:
                    for depth, number in enumerate(option_path):
                        result = await tab.evaluate(
                            _CLICK_FACEBOOK_OPTION_JS,
                            {"index": number - 1, "mode": "dialog"},
                        )
                        if not result.get("clicked"):
                            raise RuntimeError(
                                f"Facebook nested option path failed at depth {depth + 1}: "
                                f"{result.get('labels', [])}"
                            )
                        if depth < len(option_path) - 1:
                            await asyncio.sleep(1)
                    return await self._finish_facebook_submission(
                        tab, option_path, submit_requested, confirm_submit
                    )

                # Legacy UI: select its first follow-up row, press Continue,
                # then walk the requested path through any resulting sheets.
                followup = await tab.evaluate(
                    _CLICK_FACEBOOK_OPTION_JS,
                    {"index": 0, "mode": "active"},
                )
                if not followup.get("clicked"):
                    raise RuntimeError(
                        "Facebook follow-up flow did not expose a first scoped text option: "
                        f"{followup.get('labels', [])}"
                    )
                await asyncio.sleep(1)

                continue_selectors = (
                    '[role="button"][aria-label="Continue"]',
                    '[role="button"]:has-text("Continue")',
                    'button:has-text("Continue")',
                )
                clicked_continue = False
                for continue_selector in continue_selectors:
                    continue_controls = tab.locator(continue_selector)
                    for continue_index in range(await continue_controls.count()):
                        continue_control = continue_controls.nth(continue_index)
                        if await continue_control.is_visible():
                            await continue_control.click(timeout=10_000)
                            clicked_continue = True
                            break
                    if clicked_continue:
                        break
                if not clicked_continue:
                    raise RuntimeError("Facebook follow-up flow did not expose a visible Continue control")
                await asyncio.sleep(1)

                for depth, number in enumerate(option_path):
                    result = await tab.evaluate(
                        _CLICK_FACEBOOK_OPTION_JS,
                        {"index": number - 1, "mode": "active"},
                    )
                    if not result.get("clicked"):
                        raise RuntimeError(
                            f"Facebook option path failed at depth {depth + 1}: "
                            f"{result.get('labels', [])}"
                        )
                    if depth < len(option_path) - 1:
                        await asyncio.sleep(1)
                return await self._finish_facebook_submission(
                    tab, option_path, submit_requested, confirm_submit
                )
        raise RuntimeError("Facebook profile three-dot More options control was not found")

    async def _finish_facebook_submission(
        self,
        tab: Page,
        option_path: tuple[int, ...],
        submit_requested: bool,
        confirm_submit: bool,
    ) -> str:
        """Stop safely or click exactly one final Submit button after confirmation."""
        if not submit_requested:
            return "selected"

        # Facebook renders the final confirmation sheet asynchronously after the
        # last category click. Poll briefly instead of assuming the button exists
        # immediately; otherwise valid paths fail intermittently with count=0.
        deadline = time.monotonic() + 10.0
        result: dict[str, Any] = {"ready": False, "count": 0, "clicked": False}
        while time.monotonic() < deadline:
            result = await tab.evaluate(_CLICK_FACEBOOK_SUBMIT_JS, False)
            if result.get("ready"):
                break
            await asyncio.sleep(0.5)
        if not result.get("ready"):
            raise RuntimeError(
                "Facebook final Submit control was not uniquely visible; "
                f"found {result.get('count', 0)} matching controls for path {option_path}"
            )
        if confirm_submit:
            result = await tab.evaluate(_CLICK_FACEBOOK_SUBMIT_JS, True)
            if not result.get("ready") or not result.get("clicked"):
                raise RuntimeError(
                    "Facebook final Submit control disappeared before confirmation for "
                    f"path {option_path}"
                )
            return "submitted"
        return "awaiting_confirmation"

    async def _plan_video(self, prompt: str, is_music: bool) -> VideoPlan:
        """Turn a raw /video prompt into a structured creative plan via the LLM.

        Uses the same Meta-primary / Manus-fallback path as plain mentions. parse_plan
        always returns a valid plan, so even if both models fail we fall back to a plan
        built from the user's own prompt rather than aborting the video.
        """
        planner_prompt = build_planner_prompt(prompt, is_music)
        raw = ""
        if self.meta.enabled:
            try:
                raw = (await self.meta.reply(planner_prompt)).strip()
            except MetaError as error:
                print(f"Meta AI planning error, falling back to Manus: {error}")
        if not raw:
            try:
                raw = (await self.media.reply(planner_prompt)).strip()
            except MediaError as error:
                print(f"Manus planning error, using prompt-only fallback plan: {error}")
        return parse_plan(raw, prompt, is_music)

    async def _answer_mention(self, context: str) -> str:
        members = extract_members(self.transcript)
        roster = ", ".join(members) if members else "unknown (history not yet loaded)"
        roster = roster[:_CHAT_ROSTER_MAX_CHARACTERS]
        prefix = f"""You are {self.settings.bot_name}, replying inside a Messenger group.

Group members (everyone who has spoken in this group, most active first):
{roster}

Recent conversation history (oldest at the top, newest at the bottom):
---
"""
        suffix = """
---

Reply only to the newest question or request addressed to you. Match the user’s language and script exactly: Bengali, Banglish, or English. Be concise, useful, and natural.

The "Group members" list above is the complete roster of who is in this group, drawn from the entire message history — trust it when asked who is in the group or who someone is, even if that person has not spoken in the recent history shown below. For comparisons about fluency, manners, skills, or behavior, make claims only when the conversation history provides direct support. Name the observable examples briefly. If the history does not provide enough evidence, say that clearly instead of inventing a ranking, member fact, or history. Do not generate image, voice, song, or edit requests in normal chat—tell users to use the explicit slash command if relevant. Output only the message that should be posted."""
        available = max(500, _CHAT_PROMPT_MAX_CHARACTERS - len(prefix) - len(suffix))
        context = (context or "")[-available:]
        prompt = prefix + context + suffix
        # Plain mentions (simple tasks) are answered by Meta AI; media commands stay on Manus.
        # If Meta is unconfigured or errors, transparently fall back to a Manus text reply.
        if self.meta.enabled:
            try:
                answer = (await self.meta.reply(prompt)).strip()
                if answer:
                    return answer
                print("Meta AI returned an empty reply; falling back to Manus.")
            except MetaError as error:
                print(f"Meta AI reply error, falling back to Manus: {error}")
        try:
            answer = (await self.media.reply(prompt)).strip()
            return answer or "I could not prepare a reply just now—please mention me again."
        except MediaError as error:
            print(f"Manus reply error: {error}")
            return "দুঃখিত, এখন উত্তরটা তৈরি করতে পারছি না। একটু পরে আবার mention দাও।"

    @staticmethod
    def _reply_anchor(recent: str) -> str:
        """Return a distinctive text slice of the message that triggered this response.

        Detection is text-only, so this text is the only handle we have to re-locate the
        triggering message in the DOM at send-time. The newest ``_MESSAGE_PATTERN`` match
        in ``recent`` is the trigger; its group(3) is the raw message text WITHOUT the
        "Message sent … by NAME:" accessibility wrapper — which is exactly what the DOM
        bubble displays, so a contains-match on it will find the right message. Falls back
        to the last non-empty line (wrapper stripped) if the pattern does not match.
        Returns "" when nothing usable is found, so the caller skips reply mode entirely.
        """
        matches = list(_MESSAGE_PATTERN.finditer(recent or ""))
        text = ""
        for match in reversed(matches):
            candidate = " ".join((match.group(3) or "").split()).strip()
            if candidate:
                text = candidate
                break
        if not text:
            for line in reversed((recent or "").splitlines()):
                stripped = re.sub(
                    r"^.*?Message sent\s*(?:\d{1,2}:\d{2})?\s*by [^:\n]{1,60}?:\s*", "", line
                )
                stripped = " ".join(stripped.split()).strip()
                if stripped:
                    text = stripped
                    break
        # A distinctive-but-short slice: long enough to be unique, short enough that a
        # contains-match survives Messenger truncating/wrapping long bubbles.
        return text[:80]

    async def _enter_reply_mode(self, page: Page, anchor: str) -> bool:
        """Arm Messenger's native Reply on the message whose text contains ``anchor``.

        Returns True only once the composer's "Replying to …" preview is confirmed, so a
        True result guarantees the next send threads to that message. Never raises: any
        failure (message scrolled out / virtualized, hover toolbar absent, DOM changed)
        returns False and the caller falls back to a plain send. Nothing is ever dropped.
        """
        if not anchor:
            return False
        try:
            if not await page.evaluate(_TAG_REPLY_TARGET_JS, anchor):
                return False
            await asyncio.sleep(0.6)  # let the scroll-into-view settle before hovering
            for _ in range(4):
                rect = await page.evaluate(_REPLY_TARGET_RECT_JS)
                if not rect or rect["w"] <= 0 or rect["h"] <= 0:
                    return False
                # A real physical pointer move is required — Messenger ignores synthetic
                # mouse events and Playwright's forced .hover() for revealing the toolbar.
                cx = rect["x"] + rect["w"] / 2
                cy = rect["y"] + rect["h"] / 2
                await page.mouse.move(cx - 30, cy)
                await page.mouse.move(cx, cy)
                await asyncio.sleep(0.6)
                result = await page.evaluate(_CLICK_REPLY_IN_ARTICLE_JS)
                path = result.get("path")
                if path == "direct":
                    break
                if path == "opened-more":
                    await asyncio.sleep(0.8)
                    if await page.evaluate(_CLICK_REPLY_MENUITEM_JS):
                        break
                    return False
            else:
                return False
            await asyncio.sleep(1.0)
            preview = await page.evaluate(_REPLYING_PREVIEW_JS)
            if preview:
                print(f"Reply mode armed: {preview}")
                return True
            return False
        except Exception as error:  # noqa: BLE001 — reply is best-effort; never break the send
            print(f"Could not enter reply mode (falling back to plain send): {error}")
            return False

    async def _send_text(self, page: Page, text: str, reply_to: str | None = None) -> None:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                if reply_to:
                    # Best-effort: thread to the triggering message. False -> plain send.
                    await self._enter_reply_mode(page, reply_to)
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

    async def _send_media(self, page: Page, asset: MediaAsset, reply_to: str | None = None) -> None:
        """Attach and send a generated media file, CONFIRMING it actually posted.

        Attaching only stages a local preview in the composer; the send is not complete
        until that staged attachment clears (Messenger removes it once it accepts the
        message). The old implementation just slept 3s and pressed Enter without focusing
        the composer or checking anything, so it could silently fail — the file sat as an
        unsent draft while this method reported success. Now each attempt: attaches, waits
        for the attachment to stage, focuses the composer (Enter is ignored unless the
        composer is focused), presses Enter, then confirms the staged attachment CLEARS —
        a media-type-agnostic proof of delivery (works for image/voice/song/video, unlike
        counting <video> tags, which also match the composer preview). Retries the whole
        cycle and raises MediaError only if delivery is never confirmed.
        """
        last_error: Exception | None = None
        for attempt in range(1, _MEDIA_SEND_ATTEMPTS + 1):
            try:
                if reply_to:
                    # Best-effort: thread the media to the triggering message. Arm reply
                    # mode BEFORE attaching — Messenger keeps it armed until send.
                    await self._enter_reply_mode(page, reply_to)
                # Never stack onto a draft left staged by a previous failed attempt.
                if await page.evaluate(_MEDIA_STAGED_JS):
                    await self._clear_staged_media(page)
                await self._attach_media_file(page, asset)
                if not await self._wait_for(page, _MEDIA_STAGED_JS, True, _MEDIA_STAGE_TIMEOUT_S):
                    raise MediaError("the attachment never staged in the composer")
                await asyncio.sleep(_MEDIA_SETTLE_S)  # let the staged preview settle before sending
                composer = await self._composer(page)
                await composer.click(timeout=15_000)  # focus — Enter is a no-op otherwise
                await asyncio.sleep(0.3)
                await composer.press("Enter", timeout=15_000)
                if await self._wait_for(page, _MEDIA_STAGED_JS, False, _MEDIA_CONFIRM_TIMEOUT_S):
                    print(f"Media sent and confirmed delivered: {asset.filename}")
                    return
                last_error = MediaError("attachment stayed staged after Enter — not sent")
                print(f"Media send attempt {attempt} not confirmed: {last_error}")
            except MediaError as error:
                last_error = error
                print(f"Media send attempt {attempt} failed: {error}")
            except Exception as error:  # noqa: BLE001 — normalize to MediaError below
                last_error = error
                print(f"Media send attempt {attempt} errored: {error}")
            await self._clear_staged_media(page)  # tidy the composer before retrying
        raise MediaError(f"The media was generated but Messenger could not post it: {last_error}")

    async def _attach_media_file(self, page: Page, asset: MediaAsset) -> None:
        """Attach the asset via the hidden file input, or the file chooser as a fallback."""
        file_inputs = page.locator("input[type='file']")
        if await file_inputs.count():
            await file_inputs.last.set_input_files(str(asset.local_path))
            return
        attach_button = page.locator("[aria-label='Attach a file'], [aria-label*='Attach']").first
        async with page.expect_file_chooser() as chooser_info:
            await attach_button.click()
        chooser = await chooser_info.value
        await chooser.set_files(str(asset.local_path))

    async def _clear_staged_media(self, page: Page) -> None:
        """Best-effort: remove any attachment still staged in the composer (for retries)."""
        try:
            if not await page.evaluate(_MEDIA_STAGED_JS):
                return
            remove = page.locator('[aria-label="Remove attachment"]')
            deadline = time.monotonic() + _MEDIA_CLEAR_TIMEOUT_S
            while time.monotonic() < deadline and await page.evaluate(_MEDIA_STAGED_JS):
                if await remove.count():
                    try:
                        await remove.first.click(timeout=3_000)
                    except Exception:  # noqa: BLE001
                        break
                await asyncio.sleep(0.5)
        except Exception as error:  # noqa: BLE001 — clearing is best-effort
            print(f"Could not clear staged media before retry: {error}")

    async def _wait_for(self, page: Page, js: str, want: bool, timeout_s: float, poll: float = 1.0) -> bool:
        """Poll ``page.evaluate(js)`` until its truthiness equals ``want`` or time runs out."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if bool(await page.evaluate(js)) == want:
                    return True
            except Exception:  # noqa: BLE001 — transient DOM errors: keep polling
                pass
            await asyncio.sleep(poll)
        return False


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
