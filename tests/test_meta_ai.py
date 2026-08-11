import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

from messenger_bot.config import Settings
from messenger_bot.meta_ai import MetaChatClient, MetaError


def make_settings(meta_key: str | None = "test-key") -> Settings:
    """Build a Settings instance for tests without touching the environment."""
    return Settings(
        bot_name="Shahidulla Kaysar",
        group_url="https://www.facebook.com/messages/t/1/",
        user_data_dir=Path("/tmp/bot_data_dir"),
        manus_api_key="manus-key",
        manus_api_base="https://api.manus.im",
        meta_api_key=meta_key,
        meta_api_base="https://api.meta.ai",
        meta_model="muse-spark-1.2",
        meta_max_tokens=2048,
        encryption_pin=None,
        poll_interval_seconds=5.0,
        context_characters=20000,
        mention_window_characters=1200,
        media_timeout_seconds=240,
        state_file=Path("/tmp/bot_state.json"),
        output_dir=Path("/tmp/media_output"),
        history_file=Path("/tmp/bot_history.txt"),
        history_scrolls=40,
        history_characters=120000,
    )


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class ExtractContentTests(unittest.TestCase):
    def test_string_content(self):
        data = {"choices": [{"message": {"content": "  Alhamdulillah valo achi  "}}]}
        self.assertEqual(MetaChatClient._extract_content(data), "Alhamdulillah valo achi")

    def test_block_list_content(self):
        data = {"choices": [{"message": {"content": [
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ]}}]}
        self.assertEqual(MetaChatClient._extract_content(data), "hello world")

    def test_empty_choices(self):
        self.assertEqual(MetaChatClient._extract_content({"choices": []}), "")
        self.assertEqual(MetaChatClient._extract_content({}), "")

    def test_null_content(self):
        data = {"choices": [{"message": {"content": None}}]}
        self.assertEqual(MetaChatClient._extract_content(data), "")


class ReplyTests(unittest.TestCase):
    def test_reply_success(self):
        client = MetaChatClient(make_settings())
        payload = {"choices": [{"message": {"content": "hello from meta"}}]}
        with patch("messenger_bot.meta_ai.requests.post", return_value=FakeResponse(200, payload)):
            answer = asyncio.run(client.reply("hi"))
        self.assertEqual(answer, "hello from meta")

    def test_empty_reply_raises(self):
        client = MetaChatClient(make_settings())
        payload = {"choices": [{"message": {"content": ""}}]}
        with patch("messenger_bot.meta_ai.requests.post", return_value=FakeResponse(200, payload)):
            with self.assertRaises(MetaError):
                asyncio.run(client.reply("hi"))

    def test_http_error_raises(self):
        client = MetaChatClient(make_settings())
        with patch("messenger_bot.meta_ai.requests.post", return_value=FakeResponse(500, text="boom")):
            with self.assertRaises(MetaError):
                asyncio.run(client.reply("hi"))

    def test_disabled_without_key(self):
        client = MetaChatClient(make_settings(meta_key=None))
        self.assertFalse(client.enabled)
        with self.assertRaises(MetaError):
            asyncio.run(client.reply("hi"))

    def test_enabled_with_key(self):
        self.assertTrue(MetaChatClient(make_settings()).enabled)


if __name__ == "__main__":
    unittest.main()
