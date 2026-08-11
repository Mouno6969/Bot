"""Configuration for the Messenger bot.

All secrets are deliberately read from environment variables. Do not commit a real
API key, browser profile, or encryption PIN to Git.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    bot_name: str
    group_url: str
    user_data_dir: Path
    manus_api_key: str
    manus_api_base: str
    encryption_pin: str | None
    poll_interval_seconds: float
    context_characters: int
    mention_window_characters: int
    media_timeout_seconds: int
    state_file: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("BOT_PROJECT_DIR", Path.cwd())).resolve()
        api_key = os.getenv("MANUS_API_KEY", "").strip()
        group_url = os.getenv("MESSENGER_GROUP_URL", "").strip()

        if not api_key:
            raise RuntimeError("MANUS_API_KEY is not set. Add it to .env before starting the bot.")
        if not group_url:
            raise RuntimeError("MESSENGER_GROUP_URL is not set. Add the testing-group URL to .env.")

        output_dir = Path(os.getenv("BOT_OUTPUT_DIR", root / "media_output")).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            bot_name=os.getenv("BOT_NAME", "Shahidulla Kaysar").strip(),
            group_url=group_url,
            user_data_dir=Path(os.getenv("BOT_USER_DATA_DIR", root / "bot_data_dir")).expanduser().resolve(),
            manus_api_key=api_key,
            # api.manus.im is used because this authenticated account's task polling resolves there.
            manus_api_base=os.getenv("MANUS_API_BASE", "https://api.manus.im").rstrip("/"),
            encryption_pin=os.getenv("MESSENGER_ENCRYPTION_PIN", "").strip() or None,
            poll_interval_seconds=float(os.getenv("BOT_POLL_SECONDS", "5")),
            context_characters=int(os.getenv("BOT_CONTEXT_CHARACTERS", "6000")),
            mention_window_characters=int(os.getenv("BOT_MENTION_WINDOW", "1200")),
            media_timeout_seconds=int(os.getenv("BOT_MEDIA_TIMEOUT_SECONDS", "240")),
            state_file=Path(os.getenv("BOT_STATE_FILE", root / "bot_state.json")).expanduser().resolve(),
            output_dir=output_dir,
        )
