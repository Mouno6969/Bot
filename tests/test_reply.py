"""Tests for native threaded replies (messenger.py: _reply_anchor / _enter_reply_mode /
_send_text / _send_media reply_to threading).

_reply_anchor is pure and tested directly. The DOM-driven pieces use a FakePage that
records evaluate/mouse calls and simulates the "Replying to …" preview, so we verify:
reply mode is attempted only when reply_to is set, a successful arming precedes the send,
and a failure to arm still sends (fallback — a response is never dropped).
"""
import unittest
from types import SimpleNamespace
from pathlib import Path

from messenger_bot.messenger import MessengerBot


def _bot():
    """A MessengerBot with no browser/network — just enough state for the send helpers."""
    bot = object.__new__(MessengerBot)
    bot.settings = SimpleNamespace(
        state_file=Path("/tmp/reply_test_state.json"),
        group_url="https://example.test/group",
        bot_name="Shahidulla Kaysar",
    )
    bot.state = SimpleNamespace(last_reply="", save=lambda *a, **k: None)
    return bot


class FakeLocator:
    """Minimal stand-in for a Playwright composer/file-input locator."""

    def __init__(self, page, name):
        self._page = page
        self._name = name

    @property
    def last(self):
        return self

    def nth(self, index):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def click(self, **kwargs):
        self._page.events.append(f"{self._name}.click")

    async def fill(self, text, **kwargs):
        self._page.events.append(f"{self._name}.fill:{text[:20]}")

    async def press(self, key, **kwargs):
        self._page.events.append(f"{self._name}.press:{key}")

    async def set_input_files(self, path, **kwargs):
        self._page.events.append(f"{self._name}.set_files")


class FakeMouse:
    def __init__(self, page):
        self._page = page

    async def move(self, x, y):
        self._page.events.append("mouse.move")


class FakePage:
    """Records the reply/compose interaction. ``arm_ok`` decides whether reply mode
    successfully arms (i.e. whether the 'Replying to …' preview appears)."""

    def __init__(self, arm_ok=True, tag_found=True):
        self.arm_ok = arm_ok
        self.tag_found = tag_found
        self.events = []
        self.mouse = FakeMouse(self)

    def locator(self, selector):
        name = "file" if "file" in selector else "composer"
        return FakeLocator(self, name)

    async def evaluate(self, script, *args):
        # Dispatch purely on distinctive substrings of the module-level JS constants.
        if "data-reply-target" in script and "closest" in script:  # tag target
            self.events.append("tag")
            return True if self.tag_found else None
        if "getBoundingClientRect" in script and "return {x:" in script:  # rect
            return {"x": 100.0, "y": 200.0, "w": 400.0, "h": 120.0}
        if "Reply to this message" in script:  # click reply control in article
            self.events.append("click-reply-control")
            return {"path": "direct"}
        if "menuitem" in script:  # reply menuitem
            self.events.append("click-reply-menuitem")
            return True
        if "Replying to" in script:  # preview probe
            return "Replying to kyare kamkoro" if self.arm_ok else None
        return None


LABEL = "Enter, Message sent 17:32 by kyare kamkoro"


class ReplyAnchorTests(unittest.TestCase):
    def test_extracts_newest_message_text(self):
        recent = (
            "Message sent 17:30 by alice: first thing\n"
            "Message sent 17:32 by bob: @Shahidulla /image a red bird\n"
        )
        self.assertEqual(MessengerBot._reply_anchor(recent), "@Shahidulla /image a red bird")

    def test_strips_the_message_sent_wrapper(self):
        # The anchor is the raw bubble text — never the "Message sent … by NAME:" wrapper.
        anchor = MessengerBot._reply_anchor("Message sent 09:01 by Carol: hello world")
        self.assertEqual(anchor, "hello world")
        self.assertNotIn("Message sent", anchor)

    def test_caps_length_to_a_distinctive_slice(self):
        long = "x" * 500
        anchor = MessengerBot._reply_anchor(f"Message sent 10:00 by Dan: {long}")
        self.assertEqual(len(anchor), 80)
        self.assertTrue(set(anchor) == {"x"})

    def test_collapses_whitespace(self):
        anchor = MessengerBot._reply_anchor("Message sent 10:00 by Dan:   many    spaces\there")
        self.assertEqual(anchor, "many spaces here")

    def test_last_line_fallback_without_pattern(self):
        anchor = MessengerBot._reply_anchor("no pattern here\njust a trailing line")
        self.assertEqual(anchor, "just a trailing line")

    def test_empty_and_garbage_return_empty(self):
        self.assertEqual(MessengerBot._reply_anchor(""), "")
        self.assertEqual(MessengerBot._reply_anchor("   \n  \n"), "")

    def test_message_with_no_text_is_skipped_for_earlier_one(self):
        # A boundary line "… by NAME" (no colon/text) must not become the anchor.
        recent = "Message sent 08:00 by Eve: real content\nMessage sent 08:01 by Frank\n"
        self.assertEqual(MessengerBot._reply_anchor(recent), "real content")


class EnterReplyModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_anchor_never_touches_the_dom(self):
        bot, page = _bot(), FakePage()
        self.assertFalse(await bot._enter_reply_mode(page, ""))
        self.assertEqual(page.events, [])

    async def test_arms_reply_mode_on_success(self):
        bot, page = _bot(), FakePage(arm_ok=True)
        self.assertTrue(await bot._enter_reply_mode(page, "some text"))
        self.assertIn("tag", page.events)
        self.assertIn("click-reply-control", page.events)

    async def test_returns_false_when_preview_never_appears(self):
        bot, page = _bot(), FakePage(arm_ok=False)
        self.assertFalse(await bot._enter_reply_mode(page, "some text"))

    async def test_returns_false_when_message_not_found(self):
        bot, page = _bot(), FakePage(tag_found=False)
        self.assertFalse(await bot._enter_reply_mode(page, "vanished text"))
        self.assertEqual(page.events, ["tag"])


class SendThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_text_arms_reply_before_sending(self):
        bot, page = _bot(), FakePage(arm_ok=True)
        await bot._send_text(page, "hi there", reply_to="trigger text")
        # Reply arming happens, then the composer is filled and Enter pressed.
        self.assertIn("tag", page.events)
        self.assertLess(page.events.index("tag"), page.events.index("composer.fill:hi there"))
        self.assertIn("composer.press:Enter", page.events)

    async def test_send_text_without_reply_to_skips_reply_mode(self):
        bot, page = _bot(), FakePage()
        await bot._send_text(page, "hi", reply_to=None)
        self.assertNotIn("tag", page.events)
        self.assertIn("composer.press:Enter", page.events)

    async def test_send_text_falls_back_when_reply_mode_fails(self):
        bot, page = _bot(), FakePage(arm_ok=False)  # arming fails
        await bot._send_text(page, "still delivered", reply_to="trigger")
        # The response is NEVER dropped — it sends as a plain message.
        self.assertIn("composer.fill:still delivered", page.events)
        self.assertIn("composer.press:Enter", page.events)

    async def test_send_media_arms_reply_before_attaching(self):
        bot, page = _bot(), FakePage(arm_ok=True)
        asset = SimpleNamespace(local_path=Path("/tmp/out.mp4"), filename="out.mp4")
        await bot._send_media(page, asset, reply_to="trigger text")
        self.assertIn("tag", page.events)
        self.assertLess(page.events.index("tag"), page.events.index("file.set_files"))
        self.assertIn("composer.press:Enter", page.events)

    async def test_send_media_without_reply_to_skips_reply_mode(self):
        bot, page = _bot(), FakePage()
        asset = SimpleNamespace(local_path=Path("/tmp/out.mp4"), filename="out.mp4")
        await bot._send_media(page, asset, reply_to=None)
        self.assertNotIn("tag", page.events)
        self.assertIn("file.set_files", page.events)


if __name__ == "__main__":
    unittest.main()
