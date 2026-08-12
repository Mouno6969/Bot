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

    async def generate_video(self, prompt: str, audio_kind: RequestKind) -> MediaAsset:
        """Compose a video locally from a generated image plus a generated audio track.

        Manus native video is quota-limited to a couple of clips a day on the free plan,
        so instead we generate the two cheap-and-plentiful parts — an image and either a
        spoken voice (audio_kind=VOICE) or an original song (audio_kind=SING) — from the
        SAME prompt, then mux them into an MP4 with ffmpeg. The two generations run
        concurrently; the mux is a fast local step.
        """
        if audio_kind not in {RequestKind.VOICE, RequestKind.SING}:
            raise MediaError(f"A composed video's audio must be voice or song, not {audio_kind.value}.")
        image_asset, audio_asset = await asyncio.gather(
            self.generate(RequestKind.IMAGE, prompt),
            self.generate(audio_kind, prompt),
        )
        return await asyncio.to_thread(
            self._compose_video, image_asset.local_path, audio_asset.local_path
        )

    def _compose_video(self, image_path: Path, audio_path: Path) -> MediaAsset:
        """Loop a still image for the audio's duration and mux into one H.264/AAC MP4."""
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
        # for a short grace window after the first "stopped" so the trailing file lands.
        stopped_since: float | None = None
        grace_seconds = 30

        while time.monotonic() < deadline:
            await asyncio.sleep(4)
            data = await asyncio.to_thread(self._list_messages, task_id)
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

            if self._latest_status(data) == "stopped":
                now = time.monotonic()
                if stopped_since is None:
                    stopped_since = now
                elif now - stopped_since >= grace_seconds:
                    break
            else:
                stopped_since = None

        hint = f" Generator said: {latest_message}" if latest_message else ""
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
