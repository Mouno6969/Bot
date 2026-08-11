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
    meta_api_key: str | None
    meta_api_base: str
    meta_model: str
    meta_max_tokens: int
    encryption_pin: str | None
    poll_interval_seconds: float
    context_characters: int
    mention_window_characters: int
    media_timeout_seconds: int
    state_file: Path
    output_dir: Path
    history_file: Path
    history_scrolls: int
    history_characters: int

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
            # Meta AI handles plain (non-command) mentions. If META_API_KEY is unset the bot
            # transparently falls back to Manus for chat, so this stays optional and non-breaking.
            meta_api_key=os.getenv("META_API_KEY", "").strip() or None,
            meta_api_base=os.getenv("META_API_BASE", "https://api.meta.ai").rstrip("/"),
            meta_model=os.getenv("META_MODEL", "muse-spark-1.2").strip(),
            # muse-spark is a reasoning model that spends most of its budget on hidden reasoning
            # tokens before emitting visible text, so a low cap yields empty replies. Keep it high.
            meta_max_tokens=int(os.getenv("META_MAX_TOKENS", "2048")),
            encryption_pin=os.getenv("MESSENGER_ENCRYPTION_PIN", "").strip() or None,
            poll_interval_seconds=float(os.getenv("BOT_POLL_SECONDS", "5")),
            context_characters=int(os.getenv("BOT_CONTEXT_CHARACTERS", "6000")),
            mention_window_characters=int(os.getenv("BOT_MENTION_WINDOW", "1200")),
            media_timeout_seconds=int(os.getenv("BOT_MEDIA_TIMEOUT_SECONDS", "240")),
            state_file=Path(os.getenv("BOT_STATE_FILE", root / "bot_state.json")).expanduser().resolve(),
            output_dir=output_dir,
            history_file=Path(os.getenv("BOT_HISTORY_FILE", root / "bot_history.txt")).expanduser().resolve(),
            history_scrolls=int(os.getenv("BOT_HISTORY_SCROLLS", "40")),
            history_characters=int(os.getenv("BOT_HISTORY_CHARACTERS", "120000")),
        )
