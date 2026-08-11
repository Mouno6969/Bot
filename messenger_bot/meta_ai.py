"""Meta AI chat client for plain (non-command) Messenger mentions.

Media commands (/image, /voice, /sing, /edit) continue to use Manus. Only normal
mentions — a member @-mentioning the bot with an ordinary message — are answered
here, through Meta's OpenAI-compatible chat completions endpoint.
"""

from __future__ import annotations

import asyncio
from typing import Any

import requests

from .config import Settings


class MetaError(RuntimeError):
    """Raised when Meta AI cannot produce a usable text reply."""


class MetaChatClient:
    """Thin client over Meta AI's /v1/chat/completions (OpenAI-compatible)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.headers = {
            "Authorization": f"Bearer {settings.meta_api_key}",
            "Content-Type": "application/json",
        }

    @property
    def enabled(self) -> bool:
        return bool(self.settings.meta_api_key)

    async def reply(self, prompt: str) -> str:
        """Return Meta AI's reply text for a single-turn prompt."""
        if not self.enabled:
            raise MetaError("Meta AI is not configured (META_API_KEY is unset).")
        return await asyncio.to_thread(self._complete, prompt)

    def _complete(self, prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.meta_model,
            "max_tokens": self.settings.meta_max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = requests.post(
                f"{self.settings.meta_api_base}/v1/chat/completions",
                headers=self.headers,
                json=payload,
                timeout=self.settings.media_timeout_seconds,
            )
        except requests.RequestException as error:
            raise MetaError(f"Meta AI request failed: {error}") from error

        if not response.ok:
            raise MetaError(f"Meta AI returned HTTP {response.status_code}: {response.text[:300]}")

        try:
            data = response.json()
        except ValueError as error:
            raise MetaError(f"Meta AI returned non-JSON response: {error}") from error

        content = self._extract_content(data)
        if not content:
            raise MetaError("Meta AI returned an empty reply (raise META_MAX_TOKENS if this persists).")
        return content

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """Pull the assistant text out of an OpenAI-style chat completion response."""
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        # Some gateways return content as a list of typed blocks; concatenate any text parts.
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") in (None, "text")
            ]
            return "".join(parts).strip()
        return ""
