import shutil
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
import asyncio

from messenger_bot.media import ManusMediaClient, MediaAsset, MediaError
from messenger_bot.router import RequestKind, missing_argument_text, parse_request
from messenger_bot.video_plan import VideoPlan


def _plan(scenes, script="say hello", mood="cinematic"):
    return VideoPlan(idea="an idea", scenes=list(scenes), script=script, mood=mood)


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
            await client.generate_video(_plan(["a", "b"]), RequestKind.IMAGE)

    async def test_generates_one_image_per_scene_plus_audio_then_composes(self):
        client = self._client()
        image_calls = []
        audio_calls = []

        async def fake_generate(kind, instruction, source_image=None):
            if kind == RequestKind.IMAGE:
                image_calls.append(instruction)
            else:
                audio_calls.append((kind, instruction))
            return MediaAsset(local_path=Path(f"/tmp/{kind.value}_{instruction}.bin"), media_type=kind.value, filename="x.bin")

        composed = MediaAsset(local_path=Path("/tmp/out.mp4"), media_type="video", filename="out.mp4")
        seen = {}
        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_cinematic_video = lambda imgs, aud, mood: seen.update(  # type: ignore[assignment]
            n_images=len(imgs), mood=mood
        ) or composed

        plan = _plan(["scene one", "scene two", "scene three"], script="narrate this", mood="energetic")
        result = await client.generate_video(plan, RequestKind.VOICE)

        self.assertEqual(result.media_type, "video")
        # One image per scene, from the scene prompts (not the raw user prompt).
        self.assertEqual(image_calls, ["scene one", "scene two", "scene three"])
        # Audio built from the plan's script, using the requested audio kind.
        self.assertEqual(audio_calls, [(RequestKind.VOICE, "narrate this")])
        # The compositor receives every scene image and the mood.
        self.assertEqual(seen, {"n_images": 3, "mood": "energetic"})

    async def test_musicvideo_uses_song_for_audio(self):
        client = self._client()
        kinds = []

        async def fake_generate(kind, instruction, source_image=None):
            kinds.append(kind)
            return MediaAsset(local_path=Path("/tmp/x"), media_type="x", filename="x")

        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_cinematic_video = lambda imgs, aud, mood: MediaAsset(Path("/tmp/o.mp4"), "video", "o.mp4")  # type: ignore[assignment]
        await client.generate_video(_plan(["a", "b"]), RequestKind.SING)
        self.assertIn(RequestKind.SING, kinds)
        self.assertEqual(kinds.count(RequestKind.IMAGE), 2)

    async def test_tolerates_a_failed_scene_image(self):
        # One of three scene images fails; the video is still composed from the other two.
        client = self._client()
        seen = {}

        async def fake_generate(kind, instruction, source_image=None):
            if kind == RequestKind.IMAGE and instruction == "scene two":
                raise MediaError("scene two failed")
            return MediaAsset(local_path=Path(f"/tmp/{instruction}.bin"), media_type=kind.value, filename="x")

        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_cinematic_video = lambda imgs, aud, mood: seen.update(n=len(imgs)) or MediaAsset(  # type: ignore[assignment]
            Path("/tmp/o.mp4"), "video", "o.mp4"
        )
        result = await client.generate_video(_plan(["scene one", "scene two", "scene three"]), RequestKind.VOICE)
        self.assertEqual(result.media_type, "video")
        self.assertEqual(seen["n"], 2)  # composed from the 2 surviving images

    async def test_all_images_failing_raises(self):
        client = self._client()

        async def fake_generate(kind, instruction, source_image=None):
            if kind == RequestKind.IMAGE:
                raise MediaError("image failed")
            return MediaAsset(local_path=Path("/tmp/a.mp3"), media_type="audio", filename="a.mp3")

        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_cinematic_video = lambda *a: self.fail("should not compose")  # type: ignore[assignment]
        with self.assertRaises(MediaError):
            await client.generate_video(_plan(["a", "b"]), RequestKind.VOICE)

    async def test_failed_audio_raises(self):
        client = self._client()

        async def fake_generate(kind, instruction, source_image=None):
            if kind in (RequestKind.VOICE, RequestKind.SING):
                raise MediaError("audio failed")
            return MediaAsset(local_path=Path("/tmp/i.png"), media_type="image", filename="i.png")

        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_cinematic_video = lambda *a: self.fail("should not compose")  # type: ignore[assignment]
        with self.assertRaises(MediaError):
            await client.generate_video(_plan(["a", "b"]), RequestKind.VOICE)

    async def test_a_transient_image_failure_is_retried_then_succeeds(self):
        # A scene image fails on its first attempt but succeeds on the retry; the video is
        # then composed from all three images (no scene dropped).
        client = self._client()
        attempts = {}
        seen = {}

        async def fake_generate(kind, instruction, source_image=None):
            attempts[instruction] = attempts.get(instruction, 0) + 1
            if kind == RequestKind.IMAGE and instruction == "scene two" and attempts[instruction] == 1:
                raise MediaError("transient stall")
            return MediaAsset(local_path=Path(f"/tmp/{instruction}.bin"), media_type=kind.value, filename="x")

        client.generate = fake_generate  # type: ignore[assignment]
        client._RETRY_BACKOFF_SECONDS = 0  # don't actually sleep in the test
        client._compose_cinematic_video = lambda imgs, aud, mood: seen.update(n=len(imgs)) or MediaAsset(  # type: ignore[assignment]
            Path("/tmp/o.mp4"), "video", "o.mp4"
        )
        result = await client.generate_video(
            _plan(["scene one", "scene two", "scene three"]), RequestKind.VOICE
        )
        self.assertEqual(result.media_type, "video")
        self.assertEqual(seen["n"], 3)  # the retried scene recovered
        self.assertEqual(attempts["scene two"], 2)  # exactly one retry

    async def test_concurrent_generations_are_capped(self):
        # No more than _MAX_CONCURRENT_JOBS generations may run at once, even with 3
        # scenes + audio queued — the free plan stalls when overloaded.
        client = self._client()
        active = 0
        peak = 0

        async def fake_generate(kind, instruction, source_image=None):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)  # yield so other jobs can interleave
            active -= 1
            return MediaAsset(local_path=Path(f"/tmp/{instruction}.bin"), media_type=kind.value, filename="x")

        client.generate = fake_generate  # type: ignore[assignment]
        client._compose_cinematic_video = lambda *a: MediaAsset(Path("/tmp/o.mp4"), "video", "o.mp4")  # type: ignore[assignment]
        await client.generate_video(_plan(["scene one", "scene two", "scene three"]), RequestKind.VOICE)
        self.assertLessEqual(peak, ManusMediaClient._MAX_CONCURRENT_JOBS)


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required for the compose integration test")
class ComposeVideoIntegrationTests(unittest.TestCase):
    COLORS = ["navy", "darkgreen", "maroon"]

    def _client(self):
        client = object.__new__(ManusMediaClient)
        client.settings = SimpleNamespace(output_dir=Path("/tmp"))
        return client

    def _fixtures(self, n_images, audio_seconds=6):
        images = []
        for i in range(n_images):
            p = Path(f"/tmp/_vtest_img_{i}.png")
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                 "-i", f"color=c={self.COLORS[i % len(self.COLORS)]}:s=640x480:d=1",
                 "-frames:v", "1", str(p)], check=True)
            images.append(p)
        aud = Path("/tmp/_vtest_aud.mp3")
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency=440:duration={audio_seconds}", "-c:a", "libmp3lame", str(aud)], check=True)
        return images, aud

    def _assert_video_and_audio(self, asset, expected_seconds=None):
        self.assertTrue(asset.filename.endswith(".mp4"))
        self.assertGreater(asset.local_path.stat().st_size, 0)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(asset.local_path)],
            capture_output=True, text=True)
        self.assertIn("video", probe.stdout)
        self.assertIn("audio", probe.stdout)
        if expected_seconds is not None:
            duration = max(float(x) for x in probe.stdout.split() if _isfloat(x))
            self.assertAlmostEqual(duration, expected_seconds, delta=1.0)

    def test_cinematic_two_scenes(self):
        images, aud = self._fixtures(2, audio_seconds=6)
        asset = self._client()._compose_cinematic_video(images, aud, "cinematic")
        self._assert_video_and_audio(asset, expected_seconds=6)
        asset.local_path.unlink(missing_ok=True)

    def test_cinematic_three_scenes_energetic(self):
        images, aud = self._fixtures(3, audio_seconds=9)
        asset = self._client()._compose_cinematic_video(images, aud, "energetic")
        self._assert_video_and_audio(asset, expected_seconds=9)
        asset.local_path.unlink(missing_ok=True)

    def test_cinematic_failure_falls_back_to_simple_mux(self):
        # Point the cinematic path at a missing image so ffmpeg fails; it must still
        # deliver a playable video via the simple still-image fallback.
        images, aud = self._fixtures(2, audio_seconds=3)
        bogus = [Path("/tmp/_does_not_exist_0.png"), Path("/tmp/_does_not_exist_1.png")]
        # First image used by the fallback must exist, so make bogus[0] real.
        bogus[0] = images[0]
        asset = self._client()._compose_cinematic_video(bogus, aud, "cinematic")
        self._assert_video_and_audio(asset)
        asset.local_path.unlink(missing_ok=True)

    def test_simple_mux_fallback_directly(self):
        images, aud = self._fixtures(1, audio_seconds=2)
        asset = self._client()._compose_video(images[0], aud)
        self._assert_video_and_audio(asset)
        asset.local_path.unlink(missing_ok=True)


def _isfloat(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    unittest.main()
