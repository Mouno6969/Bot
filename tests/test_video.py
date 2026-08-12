import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

from messenger_bot.media import ManusMediaClient, MediaAsset, MediaError
from messenger_bot.router import RequestKind, missing_argument_text, parse_request


class VideoRoutingTests(unittest.TestCase):
    def test_video_and_musicvideo_route_correctly(self):
        self.assertEqual(parse_request("@Shahidulla /video shubho sokal").kind, RequestKind.VIDEO)
        self.assertEqual(parse_request("@Shahidulla /musicvideo gaan").kind, RequestKind.MUSICVIDEO)
        self.assertEqual(parse_request("@Shahidulla /VIDEO hi").kind, RequestKind.VIDEO)

    def test_musicvideo_is_not_swallowed_by_video(self):
        # The /video alternative must not partial-match inside /musicvideo.
        r = parse_request("@Shahidulla /musicvideo party time")
        self.assertEqual(r.kind, RequestKind.MUSICVIDEO)
        self.assertEqual(r.argument, "party time")

    def test_argument_is_bounded_to_the_command_line(self):
        scrape = "@Shahidulla /video shubho sokal\nCompose\nChat members\n"
        self.assertEqual(parse_request(scrape).argument, "shubho sokal")

    def test_missing_argument_examples_exist(self):
        self.assertIn("/video", missing_argument_text(RequestKind.VIDEO))
        self.assertIn("/musicvideo", missing_argument_text(RequestKind.MUSICVIDEO))


class MediaTypeHelperTests(unittest.TestCase):
    def test_video_content_type_is_detected(self):
        self.assertEqual(ManusMediaClient._media_type({"content_type": "video/mp4"}), "video")
        self.assertEqual(ManusMediaClient._media_type({"type": "video"}), "video")

    def test_video_filename_defaults_to_mp4(self):
        self.assertTrue(ManusMediaClient._safe_filename("clip", "video").endswith(".mp4"))
        self.assertTrue(ManusMediaClient._safe_filename("clip", "image").endswith(".png"))
        self.assertTrue(ManusMediaClient._safe_filename("clip", "audio").endswith(".mp3"))


class GenerateVideoTests(unittest.IsolatedAsyncioTestCase):
    def _client(self):
        client = object.__new__(ManusMediaClient)
        client.settings = SimpleNamespace(output_dir=Path("/tmp"))
        return client

    async def test_rejects_non_audio_kinds(self):
        client = self._client()
        with self.assertRaises(MediaError):
            await client.generate_video("hi", RequestKind.IMAGE)

    async def test_generates_image_and_audio_then_composes(self):
        client = self._client()
        calls = []

        async def fake_generate(kind, instruction, source_image=None):
            calls.append((kind, instruction))
            return MediaAsset(local_path=Path(f"/tmp/{kind.value}.bin"), media_type=kind.value, filename=f"{kind.value}.bin")

        composed = MediaAsset(local_path=Path("/tmp/out.mp4"), media_type="video", filename="out.mp4")
        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_video = lambda img, aud: composed  # type: ignore[assignment]

        # /video uses VOICE
        result = await client.generate_video("shubho sokal", RequestKind.VOICE)
        self.assertEqual(result.media_type, "video")
        kinds = {k for k, _ in calls}
        self.assertEqual(kinds, {RequestKind.IMAGE, RequestKind.VOICE})
        # both sub-generations get the same one prompt
        self.assertTrue(all(instr == "shubho sokal" for _, instr in calls))

    async def test_musicvideo_uses_song(self):
        client = self._client()
        calls = []

        async def fake_generate(kind, instruction, source_image=None):
            calls.append(kind)
            return MediaAsset(local_path=Path("/tmp/x"), media_type="x", filename="x")

        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_video = lambda img, aud: MediaAsset(Path("/tmp/o.mp4"), "video", "o.mp4")  # type: ignore[assignment]
        await client.generate_video("gaan", RequestKind.SING)
        self.assertIn(RequestKind.SING, calls)
        self.assertIn(RequestKind.IMAGE, calls)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for the compose integration test")
class ComposeVideoIntegrationTests(unittest.TestCase):
    def test_compose_produces_a_playable_mp4(self):
        img = Path("/tmp/_vtest_img.png")
        aud = Path("/tmp/_vtest_aud.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=navy:s=320x240:d=1", "-frames:v", "1", str(img)], check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=2", "-c:a", "libmp3lame", str(aud)], check=True)

        client = object.__new__(ManusMediaClient)
        client.settings = SimpleNamespace(output_dir=Path("/tmp"))
        asset = client._compose_video(img, aud)

        self.assertTrue(asset.filename.endswith(".mp4"))
        self.assertGreater(asset.local_path.stat().st_size, 0)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", str(asset.local_path)],
            capture_output=True, text=True)
        self.assertIn("video", probe.stdout)
        self.assertIn("audio", probe.stdout)
        asset.local_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
