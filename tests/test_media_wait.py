import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from messenger_bot import media as media_module
from messenger_bot.media import ManusMediaClient, MediaAsset, MediaError
from messenger_bot.router import RequestKind


def _running():
    return {"messages": [{"type": "status_update", "status_update": {"agent_status": "running"}}]}


def _stopped(with_attachment):
    attachments = []
    if with_attachment:
        attachments = [
            {
                "type": "file",
                "content_type": "audio/mpeg",
                "filename": "song.mp3",
                "url": "https://example.test/song.mp3",
            }
        ]
    return {
        "messages": [
            {"type": "status_update", "status_update": {"agent_status": "stopped"}},
            {
                "type": "assistant_message",
                "assistant_message": {"content": "আমি গানটি তৈরি করছি।", "attachments": attachments},
            },
        ]
    }


class WaitForAssetGraceTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, responses):
        client = object.__new__(ManusMediaClient)
        client.settings = SimpleNamespace(media_timeout_seconds=360)
        # Feed listMessages one canned response per poll; hold the last one after exhaustion.
        self._responses = list(responses)

        def fake_list(_task_id):
            self._poll_count += 1
            idx = min(self._poll_count - 1, len(self._responses) - 1)
            return self._responses[idx]

        client._list_messages = fake_list  # type: ignore[assignment]
        client._download_asset = lambda url, attachment, media_type: MediaAsset(  # type: ignore[assignment]
            local_path=None, media_type=media_type, filename=attachment["filename"]
        )
        self._poll_count = 0
        return client

    async def _run(self, client):
        # Fake clock: each awaited sleep advances monotonic time, so the 30s grace
        # window elapses deterministically without any real waiting.
        clock = {"t": 0.0}

        async def fake_sleep(seconds):
            clock["t"] += seconds

        with mock.patch.object(media_module.time, "monotonic", lambda: clock["t"]), mock.patch.object(
            media_module.asyncio, "sleep", fake_sleep
        ):
            return await client._wait_for_asset("tid", RequestKind.SING)

    async def test_attachment_arriving_after_stopped_is_still_returned(self):
        # The bug: status flips to "stopped" a poll BEFORE the file message lands.
        client = self._client(
            [
                _running(),
                _stopped(with_attachment=False),  # stopped, file not committed yet
                _stopped(with_attachment=False),
                _stopped(with_attachment=True),  # file appears ~12s into the grace window
            ]
        )
        asset = await self._run(client)
        self.assertEqual(asset.filename, "song.mp3")
        self.assertEqual(asset.media_type, "audio")

    async def test_gives_up_only_after_grace_window_expires(self):
        # If the file never arrives, it must still fail — but only after polling
        # through the grace window, not on the first "stopped" cycle.
        client = self._client([_running(), _stopped(with_attachment=False)])
        with self.assertRaises(MediaError):
            await self._run(client)
        # More than the couple of polls the old immediate-break path would have done.
        self.assertGreater(self._poll_count, 4)


if __name__ == "__main__":
    unittest.main()
