"""Manus API media jobs for image, voice, song, and image-edit commands."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import asyncio
import mimetypes
import re
import subprocess
import time

import requests

from .config import Settings
from .router import RequestKind
from .video_plan import VideoPlan


class MediaError(RuntimeError):
    """Raised when a generation job cannot yield a usable media attachment."""


@dataclass(frozen=True)
class MediaAsset:
    local_path: Path
    media_type: str
    filename: str


class ManusMediaClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.headers = {
            "x-manus-api-key": settings.manus_api_key,
            "Content-Type": "application/json",
        }

    async def generate(
        self,
        kind: RequestKind,
        user_instruction: str,
        source_image: Path | None = None,
    ) -> MediaAsset:
        """Create one media task and return the first generated attachment."""
        file_id = await asyncio.to_thread(self._upload_file, source_image) if source_image else None
        task_id = await asyncio.to_thread(self._create_task, kind, user_instruction, file_id)
        return await self._wait_for_asset(task_id, kind)

    async def generate_video(self, plan: VideoPlan, audio_kind: RequestKind) -> MediaAsset:
        """Compose a cinematic video locally from a creative plan.

        Manus native video is quota-limited to a couple of clips a day on the free plan,
        so instead we generate the plan's cheap-and-plentiful parts — one image per scene
        and either a spoken voiceover (audio_kind=VOICE) or an original song
        (audio_kind=SING) built from the plan's script — then stitch them into an MP4 with
        ffmpeg: Ken Burns motion on each scene, cross-fades between scenes, a mood-based
        color grade, vignette, and fade in/out. All generations run concurrently; the mux
        is a local step. If the cinematic render fails, we fall back to a simple still mux
        so the user still gets a video rather than an error.
        """
        if audio_kind not in {RequestKind.VOICE, RequestKind.SING}:
            raise MediaError(f"A composed video's audio must be voice or song, not {audio_kind.value}.")

        # Generate every scene image plus the audio track. Observed behaviour on the free
        # plan: two concurrent tasks (one image + one audio) succeed reliably, but firing
        # all 3–4 at once makes most of them stall out ("creating the image now…" with no
        # attachment ever landing). So we cap concurrency to _MAX_CONCURRENT_JOBS and give
        # each job a retry — a stalled/500'd generation gets a second chance instead of
        # sinking the whole video. We still tolerate partial image failures and compose
        # from whatever images DID land; audio is the one hard requirement.
        semaphore = asyncio.Semaphore(self._MAX_CONCURRENT_JOBS)
        image_jobs = [self._generate_guarded(semaphore, RequestKind.IMAGE, scene) for scene in plan.scenes]
        audio_job = self._generate_guarded(semaphore, audio_kind, plan.script)
        *image_results, audio_result = await asyncio.gather(
            *image_jobs, audio_job, return_exceptions=True
        )

        if isinstance(audio_result, BaseException):
            raise MediaError(f"Could not generate the video's audio: {audio_result}")

        image_paths = []
        for index, result in enumerate(image_results):
            if isinstance(result, BaseException):
                print(f"Scene {index + 1} image failed (continuing with the rest): {result}")
            else:
                image_paths.append(result.local_path)

        if not image_paths:
            raise MediaError(f"Could not generate any scene images for the video: {image_results[0]}")
        print(f"Composing video from {len(image_paths)}/{len(plan.scenes)} scene image(s).")

        return await asyncio.to_thread(
            self._compose_cinematic_video, image_paths, audio_result.local_path, plan.mood
        )

    # The free plan reliably handles two simultaneous generations; more than that and most
    # of them stall without ever delivering a file. Cap concurrency here and retry each job
    # once so a single transient failure doesn't waste the whole render.
    _MAX_CONCURRENT_JOBS = 2
    _JOB_ATTEMPTS = 2
    _RETRY_BACKOFF_SECONDS = 5.0

    async def _generate_guarded(
        self, semaphore: asyncio.Semaphore, kind: RequestKind, instruction: str
    ) -> MediaAsset:
        """Run one generation under the concurrency cap, retrying once on failure."""
        last_error: MediaError | None = None
        for attempt in range(1, self._JOB_ATTEMPTS + 1):
            async with semaphore:
                try:
                    return await self.generate(kind, instruction)
                except MediaError as error:
                    last_error = error
                    print(
                        f"{kind.value} generation attempt {attempt}/{self._JOB_ATTEMPTS} "
                        f"failed: {error}"
                    )
            if attempt < self._JOB_ATTEMPTS:
                await asyncio.sleep(self._RETRY_BACKOFF_SECONDS)
        raise last_error if last_error else MediaError(f"{kind.value} generation failed")

    # Mood → concrete ffmpeg parameters. The LLM only picks the label; every number
    # here is code-controlled, so model output can never reach the command line.
    _MOOD_EFFECTS = {
        "calm": {"zoom_speed": 0.0008, "max_zoom": 1.15, "crossfade": 1.5, "contrast": 1.03, "saturation": 1.05, "transition": "fade"},
        "cinematic": {"zoom_speed": 0.0015, "max_zoom": 1.3, "crossfade": 1.0, "contrast": 1.1, "saturation": 1.15, "transition": "fade"},
        "energetic": {"zoom_speed": 0.0028, "max_zoom": 1.45, "crossfade": 0.6, "contrast": 1.15, "saturation": 1.3, "transition": "fadeblack"},
    }
    _FPS = 24
    _WIDTH = 1280
    _HEIGHT = 720

    def _compose_cinematic_video(self, image_paths: list[Path], audio_path: Path, mood: str) -> MediaAsset:
        """Stitch scene images into a moving, graded MP4 synced to the audio's length."""
        effects = self._MOOD_EFFECTS.get(mood, self._MOOD_EFFECTS["cinematic"])
        duration = self._probe_duration(audio_path)
        n = len(image_paths)

        # Crossfade length must stay well under a single scene's screen time; clamp it so
        # short audio never produces a negative xfade offset.
        scene_len = duration / n if n else duration
        crossfade = min(effects["crossfade"], scene_len * 0.5)
        # With N scenes overlapping by `crossfade`, total = n*clip - (n-1)*xf. Solve for the
        # per-clip length that makes the montage exactly as long as the audio.
        clip_len = (duration + (n - 1) * crossfade) / n
        clip_frames = max(2, round(clip_len * self._FPS))

        output_path = self.settings.output_dir / f"{int(time.time())}_{self._safe_filename('cinematic_video', 'video')}"
        command = self._build_cinematic_command(image_paths, audio_path, output_path, effects, duration, clip_frames, crossfade)

        try:
            result = subprocess.run(command, capture_output=True, timeout=300)
        except (subprocess.SubprocessError, OSError) as error:
            print(f"Cinematic compose crashed ({error}); falling back to simple mux.")
            return self._compose_video(image_paths[0], audio_path)
        if result.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            detail = result.stderr.decode("utf-8", "ignore").strip()[:300]
            print(f"Cinematic compose failed ({detail or 'ffmpeg error'}); falling back to simple mux.")
            return self._compose_video(image_paths[0], audio_path)
        return MediaAsset(local_path=output_path, media_type="video", filename=output_path.name)

    def _build_cinematic_command(
        self,
        image_paths: list[Path],
        audio_path: Path,
        output_path: Path,
        effects: dict[str, Any],
        duration: float,
        clip_frames: int,
        crossfade: float,
    ) -> list[str]:
        """Build the ffmpeg argv for the Ken Burns + crossfade + grade filtergraph."""
        command = ["ffmpeg", "-y", "-loglevel", "error"]
        for path in image_paths:
            command += ["-i", str(path)]
        command += ["-i", str(audio_path)]

        zoom = f"z='min(zoom+{effects['zoom_speed']},{effects['max_zoom']})'"
        # Each still is upscaled for zoom headroom, cropped to a fixed frame, then Ken-Burns
        # zoomed to a uniform size/fps so the scenes can be cross-faded together.
        ken_burns = (
            f"scale={self._WIDTH * 2}:{self._HEIGHT * 2}:force_original_aspect_ratio=increase,"
            f"crop={self._WIDTH * 2}:{self._HEIGHT * 2},"
            f"zoompan={zoom}:d={clip_frames}:s={self._WIDTH}x{self._HEIGHT}:fps={self._FPS},setsar=1"
        )
        parts = [f"[{i}:v]{ken_burns}[v{i}]" for i in range(len(image_paths))]

        # Chain cross-fades: each transition starts `crossfade` before the running clip ends.
        scene_seconds = clip_frames / self._FPS
        last_label = "v0"
        for i in range(1, len(image_paths)):
            offset = i * (scene_seconds - crossfade)
            out_label = f"x{i}"
            parts.append(
                f"[{last_label}][v{i}]xfade=transition={effects['transition']}:"
                f"duration={crossfade:.3f}:offset={offset:.3f}[{out_label}]"
            )
            last_label = out_label

        fade_out_start = max(0.0, duration - 0.5)
        parts.append(
            f"[{last_label}]eq=contrast={effects['contrast']}:saturation={effects['saturation']},"
            f"vignette,fade=t=in:st=0:d=0.5,fade=t=out:st={fade_out_start:.3f}:d=0.5[vout]"
        )

        audio_index = len(image_paths)
        command += [
            "-filter_complex", ";".join(parts),
            "-map", "[vout]", "-map", f"{audio_index}:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
        return command

    @staticmethod
    def _probe_duration(audio_path: Path) -> float:
        """Return the audio length in seconds (ffprobe), with a safe fallback."""
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, timeout=30,
            )
            value = float(result.stdout.decode("utf-8", "ignore").strip())
            if value > 0:
                return value
        except (subprocess.SubprocessError, OSError, ValueError):
            pass
        return 12.0  # a reasonable default montage length if probing fails

    def _compose_video(self, image_path: Path, audio_path: Path) -> MediaAsset:
        """Fallback: loop one still image for the audio's duration and mux to H.264/AAC."""
        filename = self._safe_filename(f"{Path(image_path).stem}_video", "video")
        output_path = self.settings.output_dir / f"{int(time.time())}_{filename}"
        command = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-i", str(image_path),
            "-i", str(audio_path),
            "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
            # yuv420p needs even dimensions; round width/height down to the nearest even number.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",  # end the video when the audio ends
            str(output_path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=180)
        except (subprocess.SubprocessError, OSError) as error:
            raise MediaError(f"Could not run the video composer: {error}") from error
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", "ignore").strip()[:300]
            raise MediaError(f"Video composition failed: {detail or 'ffmpeg returned an error.'}")
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise MediaError("The composed video file was empty.")
        return MediaAsset(local_path=output_path, media_type="video", filename=output_path.name)

    async def reply(self, prompt: str) -> str:
        """Create a lightweight text task and return its final assistant message."""
        task_id = await asyncio.to_thread(self._create_text_task, prompt)
        return await self._wait_for_text(task_id)

    def _create_text_task(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "message": {"content": [{"type": "text", "text": prompt}]},
            "agent_profile": "manus-1.6-lite",
            "interactive_mode": False,
            "hide_in_task_list": True,
            "share_visibility": "private",
            "title": "Messenger mention reply",
        }
        response = requests.post(
            f"{self.settings.manus_api_base}/v2/task.create",
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        if not response.ok:
            raise MediaError(f"Could not start the reply task: HTTP {response.status_code}: {response.text}")
        data = response.json()
        if not data.get("ok") or not data.get("task_id"):
            raise MediaError(f"Could not start the reply task: {data.get('error', {}).get('message', 'unknown error')}")
        return data["task_id"]

    async def _wait_for_text(self, task_id: str) -> str:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            await asyncio.sleep(3)
            data = await asyncio.to_thread(self._list_messages, task_id)
            if self._latest_status(data) != "stopped":
                continue
            for message in data.get("messages", []):
                if message.get("type") != "assistant_message":
                    continue
                content = (message.get("assistant_message", {}).get("content") or "").strip()
                if content:
                    return content
            break
        raise MediaError("The reply task did not return a message before timing out.")

    def _upload_file(self, path: Path) -> str:
        if not path.exists() or not path.is_file():
            raise MediaError("The attached image was no longer available for editing.")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise MediaError("The image is over 20 MB. Please send a smaller image.")

        create = requests.post(
            f"{self.settings.manus_api_base}/v2/file.upload",
            headers=self.headers,
            json={"filename": path.name},
            timeout=30,
        )
        create.raise_for_status()
        payload = create.json()
        if not payload.get("ok"):
            raise MediaError(f"Could not prepare the image upload: {payload.get('error', {}).get('message', 'unknown error')}")

        with path.open("rb") as handle:
            uploaded = requests.put(
                payload["upload_url"],
                data=handle,
                headers={"Content-Type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"},
                timeout=90,
            )
        uploaded.raise_for_status()
        return payload["file"]["id"]

    def _create_task(self, kind: RequestKind, instruction: str, file_id: str | None) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": self._task_prompt(kind, instruction)}]
        if file_id:
            content.append({"type": "file", "file_id": file_id})

        payload: dict[str, Any] = {
            "message": {"content": content},
            "agent_profile": "manus-1.6-lite",
            "interactive_mode": False,
            "hide_in_task_list": True,
            "share_visibility": "private",
            "title": f"Messenger {kind.value} command",
        }
        response = requests.post(
            f"{self.settings.manus_api_base}/v2/task.create",
            headers=self.headers,
            json=payload,
            timeout=30,
        )
        if not response.ok:
            raise MediaError(f"Could not start the media job: HTTP {response.status_code}: {response.text}")
        data = response.json()
        if not data.get("ok") or not data.get("task_id"):
            raise MediaError(f"Could not start the media job: {data.get('error', {}).get('message', 'unknown error')}")
        return data["task_id"]

    async def _wait_for_asset(self, task_id: str, kind: RequestKind) -> MediaAsset:
        deadline = time.monotonic() + self.settings.media_timeout_seconds
        latest_message = ""
        # Manus can report the job "stopped" a poll or two BEFORE the final
        # attachment message is committed to listMessages. Slow commands (sing has
        # to compose music; edit uploads a source image and re-renders it) hit this
        # race, and the old code broke on the same cycle the status flipped — losing
        # the file and reporting "finished without a usable attachment". Keep polling
        # for a grace window after the first "stopped" so the trailing file lands.
        #
        # Manus job times are highly variable (the same prompt has taken 64s and 244s
        # in back-to-back runs) and after "stopped" the generator often keeps posting
        # progress messages ("I'm preparing the single audio attachment now") before
        # the file appears. So (1) treat any NEW assistant content as fresh activity
        # and reset the grace timer — if it's still talking it's still working — and
        # (2) keep a generous 90s backstop for the trailing commit itself.
        stopped_since: float | None = None
        grace_seconds = 90
        previous_message = ""
        last_poll_error: str | None = None

        while time.monotonic() < deadline:
            await asyncio.sleep(4)
            try:
                data = await asyncio.to_thread(self._list_messages, task_id)
            except (requests.RequestException, MediaError) as error:
                # A single transient API hiccup (e.g. an intermittent HTTP 500 on
                # listMessages) must not kill a multi-minute job — especially a video,
                # which polls several generations at once. Log it and keep polling until
                # the deadline instead of aborting on the first blip.
                last_poll_error = str(error)
                print(f"Transient poll error for {kind.value} (still waiting): {error}")
                continue

            for message in data.get("messages", []):
                if message.get("type") != "assistant_message":
                    continue
                assistant_message = message.get("assistant_message", {})
                latest_message = assistant_message.get("content") or latest_message
                for attachment in assistant_message.get("attachments", []):
                    url = attachment.get("url")
                    if not url:
                        continue
                    media_type = self._media_type(attachment)
                    if self._is_expected_media(kind, media_type):
                        return await asyncio.to_thread(self._download_asset, url, attachment, media_type)

            # A quota/plan block can never resolve by waiting (Manus even retries in-task
            # and hits the same wall), so fail fast with a clear message instead of polling
            # the full timeout. Video is the common case: the free plan allows very few per day.
            if self._is_quota_message(latest_message):
                raise MediaError(self._quota_reason(kind))

            # New content since the last poll means the job is still actively working,
            # even if the status already reads "stopped" — don't let the grace run out
            # underneath a job that's still producing progress.
            if latest_message != previous_message:
                previous_message = latest_message
                stopped_since = None

            if self._latest_status(data) == "stopped":
                now = time.monotonic()
                if stopped_since is None:
                    stopped_since = now
                elif now - stopped_since >= grace_seconds:
                    break
            else:
                stopped_since = None

        hint = f" Generator said: {latest_message}" if latest_message else ""
        if last_poll_error and not latest_message:
            hint = f" Last error: {last_poll_error}"
        raise MediaError(f"The {kind.value} job finished without a usable attachment.{hint}")

    @staticmethod
    def _is_quota_message(text: str) -> bool:
        lowered = (text or "").lower()
        quota_markers = ("quota", "daily limit", "upgrade for", "free-plan", "free plan")
        unavailable = ("unavailable" in lowered or "exhausted" in lowered or "reached" in lowered
                       or "limit" in lowered)
        return unavailable and any(marker in lowered for marker in quota_markers)

    @staticmethod
    def _quota_reason(kind: RequestKind) -> str:
        return (
            f"{kind.value} generation-er ajker quota shesh (free plan-e din e khub kom "
            "banano jay). Kal abar try koro, othoba plan upgrade korle beshi banano jabe."
        )

    def _list_messages(self, task_id: str) -> dict[str, Any]:
        response = requests.get(
            f"{self.settings.manus_api_base}/v2/task.listMessages?task_id={task_id}",
            headers={"x-manus-api-key": self.settings.manus_api_key},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise MediaError(data.get("error", {}).get("message", "Could not read media-job status."))
        return data

    @staticmethod
    def _latest_status(data: dict[str, Any]) -> str | None:
        statuses = [
            item.get("status_update", {}).get("agent_status")
            for item in data.get("messages", [])
            if item.get("type") == "status_update"
        ]
        return statuses[0] if statuses else None

    @staticmethod
    def _media_type(attachment: dict[str, Any]) -> str:
        raw_type = str(attachment.get("type", "")).lower()
        content_type = str(attachment.get("content_type", "")).lower()
        if raw_type == "image" or content_type.startswith("image/"):
            return "image"
        if raw_type in {"audio", "music", "voice"} or content_type.startswith("audio/"):
            return "audio"
        if raw_type in {"video", "movie"} or content_type.startswith("video/"):
            return "video"
        return "file"

    @staticmethod
    def _is_expected_media(kind: RequestKind, media_type: str) -> bool:
        if kind in {RequestKind.IMAGE, RequestKind.EDIT}:
            return media_type == "image"
        return media_type == "audio"

    def _download_asset(self, url: str, attachment: dict[str, Any], media_type: str) -> MediaAsset:
        filename = self._safe_filename(attachment.get("filename") or "generated_media", media_type)
        output_path = self.settings.output_dir / f"{int(time.time())}_{filename}"
        response = requests.get(url, timeout=90)
        response.raise_for_status()
        output_path.write_bytes(response.content)
        if output_path.stat().st_size == 0:
            raise MediaError("The generated media file was empty.")
        return MediaAsset(local_path=output_path, media_type=media_type, filename=output_path.name)

    @staticmethod
    def _safe_filename(filename: str, media_type: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "generated_media"
        suffix = Path(clean).suffix
        if not suffix:
            default = {"image": ".png", "video": ".mp4"}.get(media_type, ".mp3")
            clean += default
        return clean

    @staticmethod
    def _task_prompt(kind: RequestKind, instruction: str) -> str:
        shared = (
            "Complete this as a Messenger attachment. Do not ask follow-up questions. "
            "Make reasonable assumptions when optional details are absent. Return exactly one generated file and no explanatory prose."
        )
        if kind == RequestKind.IMAGE:
            return (
                f"Generate exactly one high-quality image from this request: {instruction}\n\n{shared} "
                "Use a suitable composition and aspect ratio for the request."
            )
        if kind == RequestKind.VOICE:
            return (
                "Produce exactly one text-to-speech audio file that speaks ONLY the script delimited by the <script> "
                "tags below. Read the words between the tags verbatim and nothing else: do NOT read these instructions, "
                "the word 'script', the tags themselves, or any quotation marks aloud, and do not add greetings, "
                "commentary, or narration of your own. "
                "Auto-detect the script's language — Bengali (Bangladesh), Banglish, English, or a mix — and use natural, "
                "native pronunciation with correct sentence intonation. "
                "Delivery: a single warm, clear, human voice at a natural conversational pace.\n"
                f"<script>\n{instruction}\n</script>\n\n{shared}"
            )
        if kind == RequestKind.SING:
            return (
                f"Compose and fully produce exactly one original song as a finished audio file based on: {instruction}\n\n"
                "The output MUST be real music: a sung vocal melody performed over actual musical instruments — a full "
                "instrumental backing track and arrangement mixed together into one track. It must NOT be spoken word, a "
                "plain voiceover, a cappella singing with no backing, or a bare beat with no melody. "
                "Include clearly audible instrumentation (for example drums or percussion, bass, and at least one melodic "
                "instrument such as guitar, piano, or synth) under a memorable sung melody. "
                "Keep the track under 90 seconds unless the request specifies a shorter duration. "
                "Use original lyrics and do not imitate a living artist or reproduce copyrighted song lyrics. "
                "Decide the genre, tempo, key, mood, instrumentation, and song structure yourself before generating. "
                f"{shared}"
            )
        if kind == RequestKind.EDIT:
            return (
                f"Edit the attached source image according to this instruction: {instruction}\n\n"
                "Change only the requested elements and preserve the person's identity, pose, product geometry, "
                "background, lighting, color palette, perspective, and all non-target details unless the instruction explicitly changes them. "
                f"{shared}"
            )
        raise MediaError(f"Unsupported media command: {kind.value}")
